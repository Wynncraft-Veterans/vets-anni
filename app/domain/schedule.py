"""Event phase — where the active anni sits relative to its stamp.

Pure (every input passed in, no DB/FastAPI/discord), so it is unit-testable
and shared by the two consumers that must agree exactly:

* ``services/lifecycle_task`` — opens grace, then wipes at ``stamp + grace``;
* ``web/ws/board_hub`` — makes the board read-only during ``GRACE`` (only a
  per-party ``result``/``stage`` edit is allowed once the fight has started).

Phases (``stamp`` = the announced unix epoch, ``grace_seconds`` =
``settings.grace_hours``×3600):

* ``PENDING``  — ``now <= stamp``: the anni hasn't started; full board edits.
* ``GRACE``    — ``stamp < now <= stamp + grace``: in progress / results being
  recorded; board frozen except party result + stage.
* ``EXPIRED``  — ``now > stamp + grace``: the lifecycle task wipes it.
"""

from __future__ import annotations

import time
from enum import StrEnum


class EventPhase(StrEnum):
    PENDING = "pending"
    GRACE = "grace"
    EXPIRED = "expired"


def phase_of(
    stamp_epoch: int, grace_seconds: int, *, now: int | None = None
) -> EventPhase:
    """The :class:`EventPhase` for ``stamp_epoch`` at ``now`` (default: wall
    clock). The boundaries are inclusive of the *earlier* phase: exactly at the
    stamp is still ``PENDING``, exactly at ``stamp + grace`` is still
    ``GRACE`` — a one-second rounding wobble never wipes early."""
    current = int(time.time()) if now is None else now
    if current <= stamp_epoch:
        return EventPhase.PENDING
    if current <= stamp_epoch + max(0, grace_seconds):
        return EventPhase.GRACE
    return EventPhase.EXPIRED


def is_board_frozen(phase: EventPhase) -> bool:
    """True once the board is read-only (only result/stage edits allowed).

    ``board_hub`` calls this; kept here so the freeze rule lives next to the
    phase definition it depends on rather than being re-derived in the hub.
    """
    return phase is EventPhase.GRACE
