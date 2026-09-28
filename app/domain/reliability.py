"""Reliability — how far staff can count on a player in one role.

Derived, never declared (it replaced the self-assessed "build quality" the
capability form used to ask for), and staff-only: players never see it.
Pure — the caller hands in the capability and the player's setbacks.

The rule, in points:

* **Start** at the self-declared confidence: HIGH starts at the Moderate
  line (:data:`MODERATE_AT` points), anything lower at 0 — LOW reliability.
* **+1 per win** in the role (``RoleCapability.success_count``).
* **−3 per penalised setback** — a LOSS in this role, or a missed hard RSVP
  (which counts against every role). Not every setback is penalised: the
  first :data:`FREE_SETBACKS` ever are free, and so is the first in each
  calendar month. So nobody is penalised before their third, and one bad
  night a month never costs anything.

Levels: HIGH needs :data:`HIGH_MIN_WINS`+ wins *and* :data:`HIGH_AT`+
points; MODERATE needs :data:`MODERATE_AT`+ points; otherwise LOW. The prior
fades as the record grows — it is worth three wins, so after a dozen annis
it is the wins and setbacks that decide.

**Staff override** (``/staff/roles``) replaces the start: the score restarts
on the chosen tier's line at the moment it was set, and from then on only
wins and setbacks *recorded after it* move it — each judged exactly as it
otherwise would be (the free allowance still runs over the whole history).
A HIGH override vouches for the win gate; LOW/MODERATE still need
:data:`HIGH_MIN_WINS` lifetime wins to climb to HIGH.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from app.constants import ConfidenceLevel, Reliability, Role, SetbackKind

#: Points to reach MODERATE — also where HIGH confidence starts.
MODERATE_AT = 3
#: Points to reach HIGH (alongside :data:`HIGH_MIN_WINS`).
HIGH_AT = 10
#: Wins in the role before HIGH is possible at all, whatever the points.
HIGH_MIN_WINS = 10
#: Points one penalised setback costs.
SETBACK_COST = 3
#: The first this-many setbacks ever are free.
FREE_SETBACKS = 2

#: Where a staff override restarts the score: exactly on its tier's line.
_OVERRIDE_POINTS: dict[Reliability, int] = {
    Reliability.LOW: 0,
    Reliability.MODERATE: MODERATE_AT,
    Reliability.HIGH: HIGH_AT,
}


def _penalised_flags(ordered: list[datetime]) -> list[bool]:
    """For setback dates already in order, whether each one costs points.

    One is free if it is among the first :data:`FREE_SETBACKS`, or the first
    in its calendar month (UTC). The free-first ones still use up their
    month, so three in one month penalises the third, and three in three
    months penalises none.
    """
    flags: list[bool] = []
    months: set[tuple[int, int]] = set()
    for i, when in enumerate(ordered):
        when = when.astimezone(timezone.utc) if when.tzinfo else when
        month = (when.year, when.month)
        first_in_month = month not in months
        months.add(month)
        flags.append(i >= FREE_SETBACKS and not first_in_month)
    return flags


def penalised_setbacks(dates: Iterable[datetime]) -> int:
    """How many of these setbacks (by date) cost points."""
    return sum(_penalised_flags(sorted(dates)))


def level(
    confidence: ConfidenceLevel,
    wins: int,
    setbacks: Iterable[tuple[datetime, datetime]],
    *,
    override: Reliability | None = None,
    override_wins: int = 0,
    override_at: datetime | None = None,
) -> Reliability:
    """The reliability for this confidence, lifetime win count and setbacks.

    ``setbacks`` are ``(occurred_at, recorded_at)`` pairs: the anni's date
    drives the free allowance, the recording time decides whether it came
    after a staff override. With ``override`` set, ``override_wins`` is the
    win count and ``override_at`` the time when it was set.
    """
    ordered = sorted(setbacks, key=lambda s: s[0])
    flags = _penalised_flags([occurred for occurred, _ in ordered])
    if override is None:
        start = MODERATE_AT if confidence == ConfidenceLevel.HIGH else 0
        earned = wins
        penalised = sum(flags)
        gate_open = wins >= HIGH_MIN_WINS
    else:
        start = _OVERRIDE_POINTS[override]
        earned = max(0, wins - override_wins)
        penalised = sum(
            flag and override_at is not None and recorded > override_at
            for flag, (_, recorded) in zip(flags, ordered)
        )
        gate_open = override == Reliability.HIGH or wins >= HIGH_MIN_WINS
    points = start + earned - SETBACK_COST * penalised
    if gate_open and points >= HIGH_AT:
        return Reliability.HIGH
    if points >= MODERATE_AT:
        return Reliability.MODERATE
    return Reliability.LOW


def setbacks_for(role: Role, setbacks: Iterable) -> list[tuple[datetime, datetime]]:
    """``(occurred_at, recorded_at)`` for the setbacks that count against
    ``role``: its own LOSSes plus every MISSED hard RSVP (a no-show lets
    down whatever you'd have played). ``setbacks`` are
    :class:`app.db.models.Setback` rows."""
    return [
        (s.occurred_at, s.created_at)
        for s in setbacks
        if s.kind == SetbackKind.MISSED
        or (s.kind == SetbackKind.LOSS and s.role == role)
    ]


def of(capability, setbacks: Iterable) -> Reliability:
    """Reliability of a :class:`RoleCapability`, given its player's
    :class:`Setback` rows. Honours the capability's staff override."""
    return level(
        capability.confidence,
        capability.success_count,
        setbacks_for(capability.role, setbacks),
        override=capability.reliability_override,
        override_wins=capability.reliability_set_wins,
        override_at=capability.reliability_set_at,
    )
