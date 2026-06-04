"""``WapiClient`` — vets-anni's OWN Wynncraft token + ratelimit bucket.

The autouse ``_offline`` fixture in conftest stubs ``_fetch_wapi_profile`` at
the call site, which means the WapiClient class itself is otherwise never
exercised in the suite — and it carries TWO load-bearing invariants:

1. **``WAPI_TOKEN`` is sent on this client and nowhere else.** That's a hard
   rule from ``.claude/integration.md``. Test it structurally so a refactor
   that quietly drops the Authorization header (or adds it to a different
   client) fails loudly.
2. **The priority queue gives interactive lookups (HIGH=0) preference over
   background crawls (LOW=9)**, so a hourly weapons crawl never blocks a
   login.

Plus the request/response edges: 429 backoff honours ``RateLimit-Reset`` /
``Retry-After``; non-429 4xx raises :class:`WapiError`; WAPI's text/plain
JSON quirk is tolerated; ``close()`` cleans up the worker and session.

Tests bypass aiohttp's network layer by patching ``WapiClient._session``
with a :class:`FakeSession` recorder. No real HTTP, no real waits.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from app.services import wapi
from app.services.wapi import (
    PRIO_HIGH,
    PRIO_LOW,
    PRIO_NORMAL,
    WapiClient,
    WapiError,
    _header_seconds,
    get_wapi,
)
from app.settings import Settings


# ---------- aiohttp fakes ----------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, payload, headers=None):
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def json(self, content_type=None):  # WAPI sometimes mis-labels JSON
        return self._payload


class _FakeCtx:
    """Async-CM around a fake response. Records the call on the session."""

    def __init__(self, session: "FakeSession", method: str, url: str, body):
        self.session = session
        self.method = method
        self.url = url
        self.body = body

    async def __aenter__(self) -> FakeResponse:
        self.session.requests.append((self.method, self.url, self.body))
        return self.session._next()

    async def __aexit__(self, *_a):
        return False


class FakeSession:
    """Drop-in replacement for ``aiohttp.ClientSession`` for WapiClient.

    Tests queue responses with :meth:`enqueue`; the client pulls them in
    order via ``session.get(...)`` / ``session.post(...)``. Calls are
    recorded in :attr:`requests` as ``(method, url, body)`` tuples.
    """

    def __init__(self, headers=None):
        self.headers = headers or {}
        self.closed = False
        self.requests: list[tuple[str, str, object]] = []
        self._responses: list[FakeResponse] = []

    def enqueue(self, resp: FakeResponse) -> "FakeSession":
        self._responses.append(resp)
        return self

    def _next(self) -> FakeResponse:
        if not self._responses:
            raise AssertionError("FakeSession ran out of queued responses")
        return self._responses.pop(0)

    def get(self, url):
        return _FakeCtx(self, "GET", url, None)

    def post(self, url, json=None):
        return _FakeCtx(self, "POST", url, json)

    async def close(self):
        self.closed = True


# ---------- fixtures ---------------------------------------------------------


@pytest.fixture
def no_sleep(monkeypatch):
    """Patch ``asyncio.sleep`` on the wapi module to a no-op recorder.

    Captures the original first to avoid the recursion trap (``wapi.asyncio``
    *is* the global asyncio module — setattr on it would replace the real
    sleep). Returned list contains the seconds-arg of each call, in order.
    """
    real_sleep = asyncio.sleep
    calls: list[float] = []

    async def _record(seconds: float) -> None:
        calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(wapi.asyncio, "sleep", _record)
    return calls


@pytest_asyncio.fixture
async def client(no_sleep):
    """A fresh WapiClient wired with a known token but NO real session yet.

    Tests inject their own FakeSession by setting ``client._session`` before
    awaiting any request. Async teardown awaits ``close()`` so the worker
    task and session are properly joined — without it, pytest emits
    ``Task was destroyed but it is pending`` warnings between tests.
    """
    c = WapiClient()
    c._settings = Settings(_env_file=None, wapi_token="test-token-xyz")
    yield c
    await c.close()


# ---------- pure helpers -----------------------------------------------------


def test_header_seconds_prefers_ratelimit_reset_over_retry_after():
    # RateLimit-Reset wins when both present.
    assert _header_seconds(
        {"RateLimit-Reset": "5", "Retry-After": "30"}, default=1.0
    ) == 5.0


def test_header_seconds_falls_back_to_retry_after():
    assert _header_seconds({"Retry-After": "7"}, default=1.0) == 7.0


def test_header_seconds_default_when_neither_present():
    assert _header_seconds({}, default=2.0) == 2.0


def test_header_seconds_default_when_value_unparseable():
    assert _header_seconds(
        {"RateLimit-Reset": "soon"}, default=3.0
    ) == 3.0


def test_header_seconds_clamps_negative_to_zero():
    assert _header_seconds({"Retry-After": "-1"}, default=2.0) == 0.0


# ---------- auth header ------------------------------------------------------


async def test_wapi_token_is_sent_as_bearer_header(monkeypatch, no_sleep):
    """Hard rule: ``WAPI_TOKEN`` is sent on THIS client. Pin structurally."""
    captured: dict = {}

    class _CaptureCS:
        def __init__(self, *, base_url=None, headers=None, timeout=None):
            captured["base_url"] = base_url
            captured["headers"] = dict(headers or {})
            captured["timeout"] = timeout
            self.closed = False

        async def close(self):
            self.closed = True

    monkeypatch.setattr(wapi.aiohttp, "ClientSession", _CaptureCS)

    c = WapiClient()
    c._settings = Settings(_env_file=None, wapi_token="secret-abc")
    await c._get_session()

    assert captured["headers"].get("Authorization") == "Bearer secret-abc"
    assert captured["headers"].get("User-Agent") == "vets-anni"
    await c.close()


async def test_missing_wapi_token_uses_anonymous_bucket(
    monkeypatch, no_sleep, caplog
):
    """Empty token => no Authorization header. Local-dev path, but explicit."""
    import logging
    caplog.set_level(logging.WARNING, logger="anni.wapi")

    captured: dict = {}

    class _CaptureCS:
        def __init__(self, *, base_url=None, headers=None, timeout=None):
            captured["headers"] = dict(headers or {})
            self.closed = False

        async def close(self):
            self.closed = True

    monkeypatch.setattr(wapi.aiohttp, "ClientSession", _CaptureCS)

    c = WapiClient()
    c._settings = Settings(_env_file=None, wapi_token="")
    await c._get_session()

    assert "Authorization" not in captured["headers"]
    assert any("WAPI_TOKEN unset" in r.getMessage() for r in caplog.records)
    await c.close()


# ---------- request / response edges ----------------------------------------


async def test_get_json_returns_decoded_payload(client):
    sess = FakeSession().enqueue(FakeResponse(200, {"name": "Returners"}))
    client._session = sess

    out = await client.get_json("guild/Returners", priority=PRIO_HIGH)

    assert out == {"name": "Returners"}
    assert sess.requests == [("GET", "/v3/guild/Returners", None)]


async def test_post_json_sends_body(client):
    sess = FakeSession().enqueue(FakeResponse(200, [{"name": "Idol"}]))
    client._session = sess

    out = await client.post_json("item/search", {"type": ["weapon"]})

    assert out == [{"name": "Idol"}]
    assert sess.requests == [
        ("POST", "/v3/item/search", {"type": ["weapon"]})
    ]


async def test_404_raises_wapi_error(client):
    sess = FakeSession().enqueue(FakeResponse(404, None))
    client._session = sess

    with pytest.raises(WapiError, match="404"):
        await client.get_json("player/no-such-uuid")


async def test_500_raises_wapi_error(client):
    sess = FakeSession().enqueue(FakeResponse(503, None))
    client._session = sess

    with pytest.raises(WapiError, match="503"):
        await client.get_json("guild/Returners")


# ---------- 429 backoff ------------------------------------------------------


async def test_429_with_ratelimit_reset_backs_off_and_retries(client, no_sleep):
    """A 429 must not bubble: honor the reset header, sleep, retry."""
    sess = (
        FakeSession()
        .enqueue(FakeResponse(429, None, {"RateLimit-Reset": "4"}))
        .enqueue(FakeResponse(200, {"ok": True}))
    )
    client._session = sess

    out = await client.get_json("guild/Returners")

    assert out == {"ok": True}
    # 4-second backoff was awaited (via the patched sleep recorder). The
    # gate is set to ``time.monotonic() + 4`` then re-read on the retry, so
    # the actual sleep is slightly under 4.0 by however many microseconds
    # passed in between.
    assert any(3.5 <= s <= 4.0 for s in no_sleep), no_sleep
    # Both requests happened — the initial and the retry.
    assert len(sess.requests) == 2


async def test_429_exhausts_retries_then_raises(client, no_sleep):
    """Four 429s in a row (initial + 3 retries) → WapiError."""
    sess = FakeSession()
    for _ in range(4):
        sess.enqueue(FakeResponse(429, None, {"Retry-After": "1"}))
    client._session = sess

    with pytest.raises(WapiError, match="ratelimited"):
        await client.get_json("guild/Returners")

    # Four requests fired (one initial + three retries).
    assert len(sess.requests) == 4


# ---------- preemptive throttle ---------------------------------------------


async def test_ratelimit_remaining_advances_gate_for_next_call(client, no_sleep):
    """Near-exhausted bucket: push ``_not_before`` so the NEXT call waits.

    Avoids eating a 429 we could have predicted from the previous response.
    """
    sess = (
        FakeSession()
        .enqueue(FakeResponse(
            200, {"a": 1},
            {"RateLimit-Remaining": "0", "RateLimit-Reset": "9"},
        ))
        .enqueue(FakeResponse(200, {"a": 2}))
    )
    client._session = sess

    await client.get_json("guild/Returners")
    await client.get_json("guild/Returners")

    # The second call observed the gate and slept ~9s before firing.
    assert any(8.0 < s <= 9.0 for s in no_sleep), no_sleep


# ---------- priority queue ---------------------------------------------------


async def test_high_priority_is_served_before_low(client):
    """Items queued before the worker starts are popped HIGH→LOW regardless
    of insertion order. The hourly catalog crawl must not block a login."""
    order: list[str] = []

    async def fake_do(method, path, body, _retries=3):
        order.append(path)
        return {"path": path}

    # Bypass network entirely — exercise only the queue + worker.
    client._do = fake_do  # type: ignore[method-assign]

    # Pre-populate the queue WITHOUT the worker running, so the priority
    # order is exercised on extraction (not on enqueue race).
    client._queue = asyncio.PriorityQueue()
    futs = {}
    for prio, path in [
        (PRIO_LOW, "item/search/catalog"),
        (PRIO_NORMAL, "guild/Returners"),
        (PRIO_HIGH, "player/login-uuid"),
    ]:
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        futs[path] = fut
        client._queue.put_nowait(
            (prio, next(client._seq), "GET", path, None, fut)
        )

    # Start the worker now that the queue is loaded.
    client._worker = asyncio.create_task(client._run(), name="wapi-worker-test")

    await asyncio.gather(*futs.values())

    assert order == [
        "player/login-uuid",       # HIGH first
        "guild/Returners",         # NORMAL second
        "item/search/catalog",     # LOW last
    ]


async def test_same_priority_is_fifo_via_seq_tiebreaker(client):
    """The ``_seq`` counter is a unique monotonic tiebreaker so the queue
    never compares the trailing tuple fields (futures aren't orderable).

    Regression guard: dropping ``_seq`` would crash on the second enqueue
    of the same priority with ``TypeError: '<' not supported``.
    """
    order: list[str] = []

    async def fake_do(method, path, body, _retries=3):
        order.append(path)
        return None

    client._do = fake_do  # type: ignore[method-assign]
    client._queue = asyncio.PriorityQueue()
    futs = []
    for i in range(3):
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        futs.append(fut)
        client._queue.put_nowait(
            (PRIO_NORMAL, next(client._seq), "GET", f"p{i}", None, fut)
        )
    client._worker = asyncio.create_task(client._run())

    await asyncio.gather(*futs)
    assert order == ["p0", "p1", "p2"]


# ---------- worker resilience -----------------------------------------------


async def test_one_failing_request_does_not_kill_the_worker(client):
    """A WapiError on one request must surface to its awaiter — and the
    worker keeps running so the NEXT request still completes."""
    sess = (
        FakeSession()
        .enqueue(FakeResponse(500, None))   # first call fails
        .enqueue(FakeResponse(200, {"ok": 1}))
    )
    client._session = sess

    with pytest.raises(WapiError):
        await client.get_json("a")

    # Worker is still alive; next call succeeds.
    assert await client.get_json("b") == {"ok": 1}


# ---------- lifecycle --------------------------------------------------------


async def test_close_cancels_worker_and_closes_session(client):
    """``close()`` from the lifespan must clean up both. No leaked task,
    no warning about unclosed ClientSession."""
    sess = FakeSession().enqueue(FakeResponse(200, {"ok": True}))
    client._session = sess

    # Bring the worker up by running one request.
    await client.get_json("guild/Returners")
    assert client._worker is not None and not client._worker.done()

    await client.close()

    assert client._worker is None
    assert client._session is None
    assert sess.closed is True


async def test_get_wapi_returns_a_singleton():
    a = get_wapi()
    b = get_wapi()
    assert a is b
    await a.close()
    # Reset module global so other tests / runs don't see the closed instance.
    wapi._client = None
