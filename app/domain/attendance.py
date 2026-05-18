"""Attendance likelihood — the published priority table as code.

Inputs: membership tier, Core/Fill, and an *effective notice*. Output: a
visible *band* (+ user-facing label) for the General-module bottom bar. The
underlying percentage is internal and is never returned or shown to the user
(spec) — only the band crosses the domain boundary (see :func:`meta`).

Notice handling (``.claude/domain_rules.md``): only ``RSVP_HARD``/
``RSVP_SOFT`` are ever stored. ``ATTEND_EARLY``/``ATTEND_LATE`` are *derived*
from the countdown — on the user's own dashboard we frame it conditionally
("assuming you log on now, you'd be EARLY/LATE"). When several notices apply
(a soft-RSVP'd member who'd also be 1 h early) the best one wins, per the
spec precedence ``ATTEND_EARLY > RSVP_HARD > RSVP_SOFT > ATTEND_LATE``.

The derived early/late notice is only meaningful for tiers we can actually
*track* without an RSVP (the Vets tiers — roster/Discord-linked). For
Community/Ally/Other those cells are N/A: an *impossible* state, because we
can't see a guildless/ally/other player through our normal systems. They have
no "just show up" option — they must RSVP or they fall to the lowest band.
So the projection is gated by ``_PROJECTABLE_TIERS`` and never overrides (or
manufactures) a notice for a non-trackable tier.

``ATTENDANCE_TABLE`` is evaluated top-to-bottom, first match wins (exactly the
order the wynnvets.org table is published in).
"""

from __future__ import annotations

from app.constants import (
    ATTENDANCE_TABLE,
    EARLY_NOTICE_CUTOFF_SECONDS,
    LIKELIHOOD_BANDS,
    AttendanceNotice,
    MembershipTier,
)

#: Spec precedence (best -> worst). Used to pick among applicable notices.
_NOTICE_RANK: dict[AttendanceNotice, int] = {
    AttendanceNotice.ATTEND_EARLY: 0,
    AttendanceNotice.RSVP_HARD: 1,
    AttendanceNotice.RSVP_SOFT: 2,
    AttendanceNotice.ATTEND_LATE: 3,
}

#: Tiers whose attendance we can observe WITHOUT an RSVP — i.e. the tiers the
#: published table gives an ATTEND_EARLY/ATTEND_LATE cell. Derived from the
#: table so it can never drift from it. For every other tier those notices are
#: an impossible (N/A) state: the early/late projection must not apply.
_PROJECTABLE_TIERS: frozenset[MembershipTier] = frozenset(
    tier
    for rule in ATTENDANCE_TABLE
    if rule.notice in (AttendanceNotice.ATTEND_EARLY, AttendanceNotice.ATTEND_LATE)
    for tier in rule.memberships
)


def project_notice(seconds_to_anni: int | None) -> AttendanceNotice:
    """The notice implied purely by the countdown ("if you logged on now").

    ``None`` (no anni announced / generic framing) is treated as EARLY — the
    best-case generic likelihood for the always-visible General module.
    """
    if seconds_to_anni is None or seconds_to_anni >= EARLY_NOTICE_CUTOFF_SECONDS:
        return AttendanceNotice.ATTEND_EARLY
    return AttendanceNotice.ATTEND_LATE


def effective_notice(
    stored: AttendanceNotice | None,
    seconds_to_anni: int | None,
    *,
    tier: MembershipTier,
) -> AttendanceNotice | None:
    """Best (highest-precedence) notice among the stored RSVP + the projection.

    The countdown projection only applies to ``_PROJECTABLE_TIERS`` (tiers we
    can track without an RSVP). For a non-trackable tier the only signal is the
    stored RSVP; with no RSVP there is *no* notice at all (``None``) — they
    cannot "just show up", so the dashboard shows them at the lowest band
    ("Most Unlikely") rather than inferring an impossible early/late state.
    """
    candidates: list[AttendanceNotice] = []
    if tier in _PROJECTABLE_TIERS:
        candidates.append(project_notice(seconds_to_anni))
    if stored is not None:
        candidates.append(stored)
    if not candidates:
        return None
    return min(candidates, key=lambda n: _NOTICE_RANK[n])


def evaluate(
    tier: MembershipTier, *, core: bool, notice: AttendanceNotice | None
) -> int | None:
    """First matching ``ATTENDANCE_TABLE`` rule's raw percentage, or ``None``.

    ``None`` means the table does not cover this combination — an N/A cell, or
    ``notice is None`` (a non-trackable tier with no RSVP). :func:`meta` treats
    that as the lowest band ("Most Unlikely"), never an invented mid-table
    number. The percentage is internal: pass it to :func:`meta` to get the
    band that is the *only* thing ever shown to the user.
    """
    for rule in ATTENDANCE_TABLE:
        if tier not in rule.memberships:
            continue
        if rule.core is not None and rule.core != core:
            continue
        if rule.notice != notice:
            continue
        return rule.pct
    return None


def meta(pct: int | None) -> tuple[int, str]:
    """(band index, user-facing label) for a raw percentage.

    The percentage itself is **never** returned or shown — only the band
    (``1``..``6``, worst -> best). An off-table cell (``pct is None`` — an N/A
    cell, or a non-trackable tier with no RSVP) is treated as 0%: still the
    lowest band ("Most Unlikely"), *not* a distinct "not prioritised" level.
    The dashboard bar derives its fill width and colour from the band alone,
    so the exact probability is not recoverable from the UI.
    """
    if pct is None:
        pct = 0
    for index, (upper, label) in enumerate(LIKELIHOOD_BANDS, start=1):
        if pct < upper:
            return (index, label)
    # pct >= 100 never occurs in ATTENDANCE_TABLE, but stay total.
    return (len(LIKELIHOOD_BANDS), LIKELIHOOD_BANDS[-1][1])
