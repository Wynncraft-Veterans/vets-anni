"""Attendance likelihood — the published priority table as code.

Inputs: membership tier, Core/Fill, and an *effective notice*. Output: a
:class:`Likelihood` (+ bar %/label) for the General-module bottom bar.

Notice handling (``.claude/domain_rules.md``): only ``RSVP_HARD``/
``RSVP_SOFT`` are ever stored. ``ATTEND_EARLY``/``ATTEND_LATE`` are *derived*
from the countdown — on the user's own dashboard we frame it conditionally
("assuming you log on now, you'd be EARLY/LATE"). When several notices apply
(a soft-RSVP'd member who'd also be 1 h early) the best one wins, per the
spec precedence ``ATTEND_EARLY > RSVP_HARD > RSVP_SOFT > ATTEND_LATE``.

``ATTENDANCE_TABLE`` is evaluated top-to-bottom, first match wins (exactly the
order the wynnvets.org table is published in).
"""

from __future__ import annotations

from app.constants import (
    ATTENDANCE_TABLE,
    EARLY_NOTICE_CUTOFF_SECONDS,
    LIKELIHOOD_META,
    AttendanceNotice,
    Likelihood,
    MembershipTier,
)

#: Spec precedence (best -> worst). Used to pick among applicable notices.
_NOTICE_RANK: dict[AttendanceNotice, int] = {
    AttendanceNotice.ATTEND_EARLY: 0,
    AttendanceNotice.RSVP_HARD: 1,
    AttendanceNotice.RSVP_SOFT: 2,
    AttendanceNotice.ATTEND_LATE: 3,
}


def project_notice(seconds_to_anni: int | None) -> AttendanceNotice:
    """The notice implied purely by the countdown ("if you logged on now").

    ``None`` (no anni announced / generic framing) is treated as EARLY — the
    best-case generic likelihood for the always-visible General module.
    """
    if seconds_to_anni is None or seconds_to_anni >= EARLY_NOTICE_CUTOFF_SECONDS:
        return AttendanceNotice.ATTEND_EARLY
    return AttendanceNotice.ATTEND_LATE


def effective_notice(
    stored: AttendanceNotice | None, seconds_to_anni: int | None
) -> AttendanceNotice:
    """Best (highest-precedence) notice among the stored RSVP + the projection."""
    candidates = [project_notice(seconds_to_anni)]
    if stored is not None:
        candidates.append(stored)
    return min(candidates, key=lambda n: _NOTICE_RANK[n])


def evaluate(
    tier: MembershipTier, *, core: bool, notice: AttendanceNotice
) -> Likelihood | None:
    """First matching ``ATTENDANCE_TABLE`` rule's likelihood, or ``None``.

    ``None`` means the table does not cover this combination (e.g. an OTHER-
    guild Fill player) — i.e. effectively *unlikely / not prioritised*; the
    dashboard renders that explicitly rather than inventing a number.
    """
    for rule in ATTENDANCE_TABLE:
        if tier not in rule.memberships:
            continue
        if rule.core is not None and rule.core != core:
            continue
        if rule.notice != notice:
            continue
        return rule.likelihood
    return None


def meta(likelihood: Likelihood | None) -> tuple[int, str]:
    """(bar %, human label). ``None`` => the explicit "not prioritised" floor."""
    if likelihood is None:
        return (0, "Not prioritised for this anni")
    return LIKELIHOOD_META[likelihood]
