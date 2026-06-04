"""``poll_forever`` — the shared resilience contract for every poller.

Eight background pollers in :mod:`main` (wapi, online_merge, lifecycle,
presence, staff, stamp, weapons, auto_promoter) depend on three behaviours
that this module promises:

1. A raised exception in ``tick()`` is logged and swallowed so the loop
   keeps running — last-good cache stays served.
2. :class:`asyncio.CancelledError` propagates so the FastAPI lifespan can
   ``await`` the task during shutdown without hanging.
3. ``interval()`` is read FRESH every iteration so an ``AppConfig`` runtime
   override (Phase 2) takes effect without a process restart.

These are 20 lines of code that, if quietly broken, take down resilience
across the whole stack. Hence the structural pin.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.services import loop


@pytest.fixture
def fast_sleep(monkeypatch):
    """Replace ``asyncio.sleep`` *on the loop module* with a no-op recorder.

    Returns the list of seconds-arg values passed in, in order. Each call
    still yields via the REAL ``asyncio.sleep(0)`` so other tasks (notably
    an outside ``task.cancel()``) get a scheduling opportunity. We bind the
    original first because ``loop.asyncio`` is the global asyncio module —
    setattr-ing ``sleep`` on it replaces the real function as well.
    """
    real_sleep = asyncio.sleep
    calls: list[float] = []

    async def _record(seconds: float) -> None:
        calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(loop.asyncio, "sleep", _record)
    return calls


async def test_tick_exception_is_logged_and_loop_continues(fast_sleep, caplog):
    """A raised exception must NOT kill the loop. Log + move on."""
    caplog.set_level(logging.ERROR, logger="anni.poller")
    n = 0

    async def flaky() -> None:
        nonlocal n
        n += 1
        if n == 2:
            raise RuntimeError("boom")
        if n >= 4:  # stop the loop deterministically
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await loop.poll_forever("test", lambda: 1.0, flaky)

    # All four ticks ran — the RuntimeError on tick 2 did not abort.
    assert n == 4
    # The exception was logged at ERROR level via ``logger.exception``.
    msgs = [r.getMessage() for r in caplog.records if r.name == "anni.poller"]
    assert any("tick failed" in m for m in msgs), msgs


async def test_cancellation_during_sleep_propagates(fast_sleep):
    """Lifespan shutdown awaits the task; it must raise CancelledError out."""
    started = asyncio.Event()

    async def idle() -> None:
        started.set()

    task = asyncio.create_task(loop.poll_forever("test", lambda: 1.0, idle))
    await started.wait()  # one tick happened — now we're in/near sleep
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancellation_during_tick_propagates(fast_sleep):
    """Cancellation raised inside ``tick()`` must also propagate, not be
    silently logged as a generic exception. The try/except in
    ``poll_forever`` separates CancelledError from the broad swallow."""
    async def cancelling_tick() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await loop.poll_forever("test", lambda: 1.0, cancelling_tick)


async def test_interval_is_read_fresh_each_iteration(fast_sleep):
    """Runtime override: mutating the value returned by ``interval()``
    between iterations must take effect on the NEXT sleep, without a
    restart of the poller."""
    holder = [10.0]
    n = 0

    async def tick() -> None:
        nonlocal n
        n += 1
        if n == 3:
            holder[0] = 42.0  # mutate mid-loop
        if n >= 4:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await loop.poll_forever("test", lambda: holder[0], tick)

    # Sleeps 1 and 2 used the original 10.0; sleep 3 used the new 42.0.
    # (Iteration 4 raises before its sleep, so only 3 sleeps total.)
    assert fast_sleep == [10.0, 10.0, 42.0]


async def test_minimum_sleep_is_one_second(fast_sleep):
    """``max(1.0, interval())`` guards against a misconfigured zero/negative
    cadence pegging the CPU. Below-floor values clamp; above pass through."""
    n = 0

    async def tick() -> None:
        nonlocal n
        n += 1
        if n >= 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await loop.poll_forever("test", lambda: 0.1, tick)

    assert fast_sleep == [1.0, 1.0]  # 0.1 clamped to floor on both sleeps


async def test_first_tick_runs_before_first_sleep(fast_sleep):
    """Cache must be warm as soon as possible after boot — tick first, then
    sleep. (Inverting this delays every poller's first useful payload by
    one full interval.)"""
    order: list[str] = []

    async def tick() -> None:
        order.append("tick")
        if len(order) >= 2:
            raise asyncio.CancelledError

    # Re-patch with an order-tracking variant. Re-bind real sleep first to
    # avoid the same recursion trap as fast_sleep.
    real_sleep = asyncio.sleep
    real_record = fast_sleep.append

    async def _record(seconds: float) -> None:
        real_record(seconds)
        order.append("sleep")
        await real_sleep(0)

    import unittest.mock as _mock
    with _mock.patch.object(loop.asyncio, "sleep", _record):
        with pytest.raises(asyncio.CancelledError):
            await loop.poll_forever("test", lambda: 1.0, tick)

    assert order == ["tick", "sleep", "tick"]
