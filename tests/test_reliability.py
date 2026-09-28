"""Reliability — the pure rule (``app.domain.reliability``).

The anchors come straight from the spec'd behaviour: confidence sets the
start, 10+ wins before High, nobody penalised before their third setback,
and one setback a month forgiven.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.constants import ConfidenceLevel as C
from app.constants import Reliability as R
from app.constants import Role, SetbackKind
from app.domain import reliability


def _day(month: int, day: int = 1) -> datetime:
    return datetime(2026, month, day, 20, tzinfo=timezone.utc)


def _sb(when: datetime) -> tuple[datetime, datetime]:
    """A setback recorded the moment it happened: (occurred, recorded)."""
    return when, when


def _same_month(n: int) -> list[tuple[datetime, datetime]]:
    return [_sb(_day(3, d + 1)) for d in range(n)]


@pytest.mark.parametrize(
    ("confidence", "wins", "setbacks", "expected"),
    [
        # Start: confidence sets it.
        (C.LOW, 0, [], R.LOW),
        (C.MODERATE, 0, [], R.LOW),
        (C.HIGH, 0, [], R.MODERATE),
        # Wins raise it; High needs 10 whatever the confidence.
        (C.MODERATE, 3, [], R.MODERATE),
        (C.HIGH, 9, [], R.MODERATE),
        (C.LOW, 10, [], R.HIGH),
        # Two setbacks are always free.
        (C.MODERATE, 10, _same_month(2), R.HIGH),
        # The third in a month costs three wins.
        (C.MODERATE, 10, _same_month(3), R.MODERATE),
        (C.MODERATE, 13, _same_month(3), R.HIGH),
        (C.HIGH, 10, _same_month(3), R.HIGH),        # the prior cushions it
        (C.HIGH, 0, _same_month(3), R.LOW),          # 3 - 3 = 0
        (C.MODERATE, 20, _same_month(6), R.MODERATE),  # 20 - 12 = 8
        # One a month is forgiven, so a spread-out record costs nothing.
        (C.MODERATE, 10, [_sb(_day(m)) for m in range(1, 7)], R.HIGH),
    ],
)
def test_level(confidence, wins, setbacks, expected):
    assert reliability.level(confidence, wins, setbacks) is expected


def test_the_free_two_use_up_their_month():
    # Jan: one (free). Mar: three — the first is grace #2 and also Mar's free
    # one, so the next two are both penalised.
    dates = [_day(1), _day(3, 1), _day(3, 2), _day(3, 3)]
    assert reliability.penalised_setbacks(dates) == 2
    # Order of input doesn't matter.
    assert reliability.penalised_setbacks(reversed(dates)) == 2


def test_role_setbacks_are_its_losses_plus_every_miss():
    s = SimpleNamespace
    rows = [
        s(kind=SetbackKind.LOSS, role=Role.TANK, occurred_at=_day(1), created_at=_day(1)),
        s(kind=SetbackKind.LOSS, role=Role.HEALER, occurred_at=_day(2), created_at=_day(2)),
        s(kind=SetbackKind.MISSED, role=None, occurred_at=_day(3), created_at=_day(3)),
    ]
    assert reliability.setbacks_for(Role.TANK, rows) == [_sb(_day(1)), _sb(_day(3))]
    assert reliability.setbacks_for(Role.PRIMARY, rows) == [_sb(_day(3))]


# --- staff override ---------------------------------------------------------

def _level(confidence, wins, setbacks, override, *, at_wins, at):
    return reliability.level(
        confidence, wins, setbacks,
        override=override, override_wins=at_wins, override_at=at,
    )


def test_override_restarts_at_the_tier_whatever_came_before():
    """A 20-win veteran staff restart at Low is Low; a newcomer at High is
    High — the override vouches for the 10-win gate."""
    past = _same_month(6)  # would have cost 12 points
    at = _day(6)
    assert _level(C.HIGH, 20, [], R.LOW, at_wins=20, at=at) is R.LOW
    assert _level(C.LOW, 0, past, R.HIGH, at_wins=0, at=at) is R.HIGH
    assert _level(C.LOW, 0, past, R.MODERATE, at_wins=0, at=at) is R.MODERATE


def test_only_what_is_recorded_after_the_override_moves_it():
    at = _day(6)
    # Restarted at Moderate with 4 lifetime wins: 6 more since is 9 points,
    # 7 more is 10 points with 11 lifetime wins -> High.
    assert _level(C.LOW, 10, [], R.MODERATE, at_wins=4, at=at) is R.MODERATE
    assert _level(C.LOW, 11, [], R.MODERATE, at_wins=4, at=at) is R.HIGH
    # A Moderate restart doesn't vouch for the gate: 10 points on 7
    # lifetime wins is still Moderate.
    assert _level(C.LOW, 7, [], R.MODERATE, at_wins=0, at=at) is R.MODERATE
    # Restarted at High; a penalised setback recorded afterwards drops it.
    after = [_sb(_day(7, d)) for d in (1, 2, 3)]  # third in July is penalised
    assert _level(C.LOW, 0, after, R.HIGH, at_wins=0, at=at) is R.MODERATE
    # The same three recorded before the restart don't count.
    before = [_sb(_day(5, d)) for d in (1, 2, 3)]
    assert _level(C.LOW, 0, before, R.HIGH, at_wins=0, at=at) is R.HIGH


def test_setbacks_after_an_override_are_judged_as_they_otherwise_would_be():
    """The free allowance runs over the whole history, so an override
    doesn't hand out two fresh free setbacks."""
    at = _day(6, 10)
    history = [_sb(_day(6, 1)), _sb(_day(6, 2))]  # the two free ones, before
    after = [_sb(_day(6, 20))]                    # third in June: penalised
    assert _level(C.LOW, 0, history + after, R.HIGH, at_wins=0, at=at) is R.MODERATE
