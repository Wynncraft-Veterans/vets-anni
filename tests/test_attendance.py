"""The attendance-priority table is published policy — pin it.

If ``ATTENDANCE_TABLE`` or its evaluation drifts, real eligibility on the
dashboard silently changes; these lock the spec'd behaviour (first-match-wins,
notice precedence, the no-rule floor).
"""

from __future__ import annotations

from app.constants import AttendanceNotice as N
from app.constants import Likelihood as L
from app.constants import MembershipTier as M
from app.domain import attendance


def test_first_match_wins_member_early_is_guaranteed():
    # MEMBER + ATTEND_EARLY is the top row, core=None (applies to Core+Fill).
    assert attendance.evaluate(M.MEMBER, core=True, notice=N.ATTEND_EARLY) is L.VIRTUALLY_GUARANTEED
    assert attendance.evaluate(M.MEMBER, core=False, notice=N.ATTEND_EARLY) is L.VIRTUALLY_GUARANTEED


def test_core_vs_fill_splits_the_likelihood():
    assert attendance.evaluate(M.WAITLIST, core=True, notice=N.RSVP_HARD) is L.VIRTUALLY_GUARANTEED
    assert attendance.evaluate(M.WAITLIST, core=False, notice=N.RSVP_HARD) is L.MORE_OFTEN_THAN_NOT
    assert attendance.evaluate(M.COMMUNITY, core=False, notice=N.RSVP_SOFT) is L.SOMETIMES


def test_uncovered_combination_has_no_rule():
    # OTHER-guild + soft RSVP isn't in the table -> not prioritised.
    assert attendance.evaluate(M.OTHER, core=False, notice=N.RSVP_SOFT) is None
    assert attendance.meta(None) == (0, "Not prioritised for this anni")
    assert attendance.meta(L.VIRTUALLY_GUARANTEED) == (95, "Virtually guaranteed")


def test_project_notice_from_countdown():
    assert attendance.project_notice(None) is N.ATTEND_EARLY        # generic framing
    assert attendance.project_notice(7200) is N.ATTEND_EARLY        # >= 1h
    assert attendance.project_notice(600) is N.ATTEND_LATE          # < 1h


def test_effective_notice_takes_the_best_of_stored_and_projection():
    # Soft RSVP but you'd be 1h early -> EARLY wins (better precedence).
    assert attendance.effective_notice(N.RSVP_SOFT, 7200) is N.ATTEND_EARLY
    # Hard RSVP but you'd be late -> the hard RSVP still wins over LATE.
    assert attendance.effective_notice(N.RSVP_HARD, 600) is N.RSVP_HARD
    # No RSVP -> pure projection.
    assert attendance.effective_notice(None, None) is N.ATTEND_EARLY
