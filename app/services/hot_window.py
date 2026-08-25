"""Hot-window predicates — the cadence ramp + LATE-bucket + RSVP-cutoff gates.

Pure (every input passed in, no DB / FastAPI / discord), mirroring
``domain.schedule``. Three consumers must agree exactly on the answers:

* the **cadence ramp** in ``online_merge`` / ``presence_poller`` /
  ``auto_promoter``: at ``T-HOT_WINDOW_OPEN`` (default 70 min before the anni)
  every "is X online" poller switches from its normal interval to its hot
  interval, and stays there through ``stamp + grace``;
* the **monitoring switch** behind the board's ``live`` pill: at
  ``T-EARLY_NOTICE_CUTOFF`` (60 min) the label flips from "1hr+ early
  joiners" to "late players". This used to also flip new auto-placements
  into a LATE sub-bucket; that lane is gone and lateness is now a per-person
  badge on a sliding, RSVP-dependent threshold (:func:`is_late_arrival`);
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

from app.constants import (
    EARLY_NOTICE_CUTOFF_SECONDS,
    LATE_ARRIVAL_SECONDS,
    RSVP_CUTOFF_SECONDS,
)


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
    """True iff ``now`` is past the T-60 monitoring switch.

    Equivalent to ``seconds_to_anni < EARLY_NOTICE_CUTOFF_SECONDS`` — handles
    negative values during grace (always True) and bails to False for a
    missing event.

    Named for the LATE sub-bucket it used to gate. That lane is gone; this
    now only drives the board's ``live`` pill label (see
    :func:`monitoring_state`). Whether a given *person* counts as a late
    arrival is :func:`is_late_arrival`, which slides with their RSVP.
    """
    if event is None:
        return False
    current = int(time.time()) if now is None else now
    seconds_to_anni = event.stamp_epoch - current
    return seconds_to_anni < EARLY_NOTICE_CUTOFF_SECONDS


def is_late_arrival(
    event: _StampedEvent | None,
    rsvp_state: str,
    *,
    now: int | None = None,
) -> bool:
    """True iff someone turning up *now* counts as a late arrival — the
    hourglass on their card.

    The threshold slides with what they promised
    (:data:`app.constants.LATE_ARRIVAL_SECONDS`): T-15 for a hard RSVP, T-35
    for a soft one, T-50 for a walk-in nobody was told about. A retracted
    RSVP has no threshold at all and is never late.

    ``rsvp_state`` is an :class:`app.constants.RsvpState` value (the raw
    string, so this module stays enum-free like the rest of its inputs). An
    unknown value is treated as the walk-in threshold — the strictest of the
    real ones, so a vocabulary drift errs toward flagging rather than hiding.
    """
    if event is None:
        return False
    threshold = LATE_ARRIVAL_SECONDS.get(
        rsvp_state, LATE_ARRIVAL_SECONDS["none"]
    )
    if threshold is None:
        return False
    current = int(time.time()) if now is None else now
    return event.stamp_epoch - current < threshold


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
    * ``early`` — in the hot window, before the T-60 switch.
    * ``late`` — in the hot window, at-or-after the switch (incl. grace).
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
