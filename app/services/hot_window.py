"""Hot-window predicates — the cadence ramp + LATE-bucket + RSVP-cutoff gates.

Pure (every input passed in, no DB / FastAPI / discord), mirroring
``domain.schedule``. Three consumers must agree exactly on the answers:

* the **cadence ramp** in ``online_merge`` / ``presence_poller`` /
  ``auto_promoter``: at ``T-HOT_WINDOW_OPEN`` (default 70 min before the anni)
  every "is X online" poller switches from its normal interval to its hot
  interval, and stays there through ``stamp + grace``;
* the **LATE-bucket switch** in ``domain.buckets.ensure_placed`` and the RSVP
  cog: at ``T-EARLY_NOTICE_CUTOFF`` (60 min) new auto-placements flip from
  the main Unassigned lane to the LATE sub-bucket (``is_late=True``);
* the **RSVP cutoff** in ``execute_rsvp``: at ``T-RSVP_CUTOFF`` (90 min) the
  user-facing ``\\rsvp hard`` / ``\\rsvp soft`` are refused. Staff override
  (``\\rsvp set``) and revokes are unaffected.

A non-active event (``None``) is never hot and never late — outside the
window everything reads as "idle". The user-visible "monitoring" label on
the board's ``live`` pill is derived from the first two predicates; see
``app/web/board_view.snapshot``.
"""

from __future__ import annotations

import time
from typing import Protocol

from app.constants import EARLY_NOTICE_CUTOFF_SECONDS, RSVP_CUTOFF_SECONDS


class _StampedEvent(Protocol):
    """Anything with a ``stamp_epoch`` field — duck-typed so this module
    stays import-free from ``app.db.models`` (keeps it cheap to unit-test).
    """

    stamp_epoch: int


def is_hot(
    event: _StampedEvent | None,
    *,
    hot_window_open_seconds: int,
    grace_seconds: int,
    now: int | None = None,
) -> bool:
    """True iff there is an active event and ``now`` is in
    ``[stamp - hot_window_open, stamp + grace]``.

    A truthy answer means every "who is online" poller should ramp to its
    hot cadence and the auto-promoter should be actively trying to land
    new arrivals on the board.
    """
    if event is None:
        return False
    current = int(time.time()) if now is None else now
    start = event.stamp_epoch - max(0, hot_window_open_seconds)
    end = event.stamp_epoch + max(0, grace_seconds)
    return start <= current <= end


def is_late_bucket(
    event: _StampedEvent | None, *, now: int | None = None
) -> bool:
    """True iff a new auto-placement for ``event`` should land in the
    LATE sub-bucket (``is_late=True``) rather than the main Unassigned lane.

    Equivalent to ``seconds_to_anni < EARLY_NOTICE_CUTOFF_SECONDS`` — handles
    negative values during grace (always True) and bails to False for a
    missing event (no event means no auto-placements happen anyway, but the
    helper stays well-defined).
    """
    if event is None:
        return False
    current = int(time.time()) if now is None else now
    seconds_to_anni = event.stamp_epoch - current
    return seconds_to_anni < EARLY_NOTICE_CUTOFF_SECONDS


def is_rsvp_closed(
    event: _StampedEvent | None, *, now: int | None = None
) -> bool:
    """True iff ``\\rsvp hard`` / ``\\rsvp soft`` should be refused.

    Equivalent to ``seconds_to_anni < RSVP_CUTOFF_SECONDS`` — past the cutoff
    a fresh declaration of intent is too late to be useful to the organiser,
    so the user is redirected to the walk-in / late-arrival paths instead.
    Negative ``seconds_to_anni`` (grace + post-expiry) read as closed too;
    a missing event reads as open so the no-event branch in the cog handles
    the friendlier "no anni announced" message itself.

    The user-facing gate; the staff override ``\\rsvp set`` deliberately
    bypasses it. Revokes are never blocked anywhere.
    """
    if event is None:
        return False
    current = int(time.time()) if now is None else now
    seconds_to_anni = event.stamp_epoch - current
    return seconds_to_anni < RSVP_CUTOFF_SECONDS


def monitoring_state(
    event: _StampedEvent | None,
    *,
    hot_window_open_seconds: int,
    grace_seconds: int,
    now: int | None = None,
) -> str:
    """Three-state label used by the board's ``live`` pill.

    * ``idle`` — outside the hot window (T > open, or no event, or post-wipe).
    * ``early`` — in the hot window, before the LATE-bucket switch.
    * ``late`` — in the hot window, at-or-after the LATE switch (incl. grace).
    """
    if not is_hot(
        event,
        hot_window_open_seconds=hot_window_open_seconds,
        grace_seconds=grace_seconds,
        now=now,
    ):
        return "idle"
    return "late" if is_late_bucket(event, now=now) else "early"


#: Display strings paired 1:1 with :func:`monitoring_state` outputs.
MONITORING_LABEL: dict[str, str] = {
    "idle": "Live — not yet monitoring online players",
    "early": "Live — monitoring for 1hr+ early joiners",
    "late": "Live — monitoring for late players",
}


#: Process-shared "is the hot window currently open?" cache. Updated by the
#: auto-promoter every tick (which already queries the active event); read
#: by the sync interval pickers of ``online_merge`` / ``presence_poller``
#: so they can ramp without each running their own DB query inside the
#: ``poll_forever`` interval callable.
_currently_hot: bool = False


def set_currently_hot(value: bool) -> None:
    """Called by the auto-promoter at the end of each tick. The other
    ramp-able pollers read it via :func:`is_currently_hot`.

    Worst-case lag: bounded by the auto-promoter's idle cadence (default
    ~60s) outside the hot window, and by its hot cadence (~3s) inside.
    First transition after process start may see one extra normal-cadence
    tick before the ramp kicks in — harmless.
    """
    global _currently_hot
    _currently_hot = bool(value)


def is_currently_hot() -> bool:
    """Sync read of the cache above. Safe from inside a poll_forever
    ``interval`` callable (where awaiting a DB query isn't possible)."""
    return _currently_hot
