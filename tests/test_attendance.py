"""The attendance-priority table is published policy — pin it.

If ``ATTENDANCE_TABLE`` or its evaluation drifts, real eligibility on the
dashboard silently changes; these lock the spec'd behaviour: the published
per-cell percentages, first-match/notice precedence, the no-rule floor, the
percentage→band collapse (the user never sees the raw %), and the rule that
the early/late projection only applies to tiers we can actually track.
"""

from __future__ import annotations

from app.constants import AttendanceNotice as N
from app.constants import MembershipTier as M
from app.domain import attendance


def test_table_matches_the_published_percentages():
    # Spot-check every tier across the four notice columns.
    assert attendance.evaluate(M.MEMBER, core=True, notice=N.ATTEND_EARLY) == 90
    assert attendance.evaluate(M.MEMBER, core=True, notice=N.ATTEND_LATE) == 20
    assert attendance.evaluate(M.MEMBER, core=False, notice=N.RSVP_HARD) == 65
    assert attendance.evaluate(M.WAITLIST, core=True, notice=N.RSVP_HARD) == 71
    assert attendance.evaluate(M.WAITLIST, core=False, notice=N.RSVP_SOFT) == 21
    assert attendance.evaluate(M.HONOURARY, core=True, notice=N.ATTEND_EARLY) == 80
    assert attendance.evaluate(M.HONOURARY, core=False, notice=N.RSVP_SOFT) == 20
    assert attendance.evaluate(M.COMMUNITY, core=True, notice=N.RSVP_HARD) == 30
    assert attendance.evaluate(M.COMMUNITY, core=False, notice=N.RSVP_SOFT) == 0
    assert attendance.evaluate(M.ALLY, core=True, notice=N.RSVP_HARD) == 20
    assert attendance.evaluate(M.OTHER, core=True, notice=N.RSVP_HARD) == 5


def test_core_vs_fill_splits_the_percentage():
    assert attendance.evaluate(M.MEMBER, core=True, notice=N.RSVP_HARD) == 80
    assert attendance.evaluate(M.MEMBER, core=False, notice=N.RSVP_HARD) == 65


def test_na_cells_have_no_rule():
    # Community/Ally/Other have N/A for the >1hr-early and Late columns.
    assert attendance.evaluate(M.COMMUNITY, core=True, notice=N.ATTEND_EARLY) is None
    assert attendance.evaluate(M.ALLY, core=False, notice=N.ATTEND_LATE) is None
    assert attendance.evaluate(M.OTHER, core=True, notice=N.ATTEND_EARLY) is None
    # notice=None (non-trackable tier, no RSVP) also has no rule.
    assert attendance.evaluate(M.COMMUNITY, core=True, notice=None) is None


def test_meta_bands_the_percentage_and_never_returns_it():
    # meta() returns (band index, user-facing label) — the raw % never crosses
    # the boundary. Bands follow the published "Visible Sort Orders". An
    # off-table cell (None) is treated as 0% → "Most Unlikely" (no separate
    # "not prioritised" level).
    assert attendance.meta(None) == (1, "Most Unlikely")
    assert attendance.meta(0) == (1, "Most Unlikely")     # < 1%
    assert attendance.meta(5) == (2, "Very Unlikely")     # < 20%
    assert attendance.meta(16) == (2, "Very Unlikely")
    assert attendance.meta(20) == (3, "Unlikely")         # < 40%
    assert attendance.meta(30) == (3, "Unlikely")
    assert attendance.meta(40) == (4, "Likely")           # < 60%
    assert attendance.meta(50) == (4, "Likely")
    assert attendance.meta(60) == (5, "Very Likely")      # < 80%
    assert attendance.meta(71) == (5, "Very Likely")
    assert attendance.meta(80) == (6, "Most Likely")      # < 100%
    assert attendance.meta(90) == (6, "Most Likely")


def test_project_notice_from_countdown():
    assert attendance.project_notice(None) is N.ATTEND_EARLY        # generic framing
    assert attendance.project_notice(7200) is N.ATTEND_EARLY        # >= 1h
    assert attendance.project_notice(600) is N.ATTEND_LATE          # < 1h


def test_effective_notice_projection_only_for_trackable_tiers():
    # Trackable (Vets) tier: stored + projection, best precedence wins.
    assert attendance.effective_notice(N.RSVP_SOFT, 7200, tier=M.MEMBER) is N.ATTEND_EARLY
    assert attendance.effective_notice(N.RSVP_HARD, 600, tier=M.MEMBER) is N.RSVP_HARD
    assert attendance.effective_notice(None, None, tier=M.MEMBER) is N.ATTEND_EARLY

    # Non-trackable tier: the early/late projection is an impossible state and
    # must NOT apply. A hard RSVP'd community member who'd also be "early"
    # keeps their RSVP (not downgraded to the N/A early cell)...
    assert attendance.effective_notice(N.RSVP_HARD, 7200, tier=M.COMMUNITY) is N.RSVP_HARD
    assert attendance.effective_notice(N.RSVP_SOFT, 7200, tier=M.OTHER) is N.RSVP_SOFT
    # ...and with no RSVP they have no notice at all (must RSVP to count).
    assert attendance.effective_notice(None, 7200, tier=M.COMMUNITY) is None
    assert attendance.effective_notice(None, None, tier=M.ALLY) is None


def test_end_to_end_community_no_rsvp_is_most_unlikely():
    # No RSVP on a non-trackable tier: no notice -> no rule -> lowest band.
    notice = attendance.effective_notice(None, 7200, tier=M.COMMUNITY)
    pct = attendance.evaluate(M.COMMUNITY, core=True, notice=notice)
    assert attendance.meta(pct) == (1, "Most Unlikely")
