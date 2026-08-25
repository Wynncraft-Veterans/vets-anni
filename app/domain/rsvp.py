"""RSVP state — the sole writer of :class:`app.db.models.Rsvp`.

Pure DB ops: every input is a resolved :class:`AnniPlayer` + :class:`AnniEvent`
(the caller does the dazebot lookup and the active-event resolution). Mirrors
the ``domain/buckets.py`` shape — keeping every Rsvp mutation behind one module
makes the soft-delete semantics, single-instance-per-(event,player), and the
revive-on-rsvp behaviour easy to reason about from one place.

The model's ``unique_together(event, player)`` guarantees one row per
(event, player); :func:`set_rsvp` is an UPSERT and :func:`revoke` is a
soft-delete (``revoked_at``) so we keep an audit trail when staff want to know
who pulled out and when.

Only ``RSVP_HARD`` / ``RSVP_SOFT`` are ever stored on ``Rsvp.notice``
(``.claude/domain_rules.md`` — ATTEND_EARLY/ATTEND_LATE are derived from the
countdown elsewhere). :func:`set_rsvp` refuses any other notice.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.constants import AttendanceNotice, RsvpState
from app.db.models import AnniEvent, AnniPlayer, Rsvp

#: The only notice values that may be stored on ``Rsvp.notice``.
_STORABLE: frozenset[AttendanceNotice] = frozenset(
    {AttendanceNotice.RSVP_HARD, AttendanceNotice.RSVP_SOFT}
)


async def get_current(player: AnniPlayer, event: AnniEvent) -> Rsvp | None:
    """The active (non-revoked) RSVP for ``(event, player)``, or ``None``.

    A revoked row is treated as "no RSVP" — callers should never resurrect a
    revoked row except via :func:`set_rsvp` (which clears ``revoked_at``).
    """
    return await Rsvp.filter(
        event=event, player=player, revoked_at__isnull=True
    ).first()


async def set_rsvp(
    player: AnniPlayer, event: AnniEvent, notice: AttendanceNotice
) -> Rsvp:
    """Upsert the RSVP and clear any prior soft-delete.

    Idempotent for repeats (same notice, same player, same event => the row's
    ``updated_at`` advances and that's it). A previously revoked row is
    *revived* by clearing ``revoked_at`` so we don't accumulate stale rows.
    """
    if notice not in _STORABLE:
        raise ValueError(
            f"Rsvp.notice must be RSVP_HARD or RSVP_SOFT (got {notice!r})"
        )
    row, _created = await Rsvp.update_or_create(
        event=event, player=player,
        defaults={"notice": notice, "revoked_at": None, "source": "discord"},
    )
    return row


async def revoke(player: AnniPlayer, event: AnniEvent) -> Rsvp | None:
    """Soft-delete the active RSVP if any; no-op when there isn't one.

    Returns the row that was just revoked (so callers can render "you had a
    HARD RSVP, now cleared"), or ``None`` when nothing was active.
    """
    row = await get_current(player, event)
    if row is None:
        return None
    row.revoked_at = datetime.now(timezone.utc)
    await row.save(update_fields=["revoked_at", "updated_at"])
    return row


def state_of(notice: AttendanceNotice | None, revoked: bool) -> RsvpState:
    """Collapse one ``Rsvp`` row (or its absence) to a :class:`RsvpState`.

    ``revoked`` wins over the stored notice on purpose: once someone pulls
    out, "they retracted" is the fact an organiser is acting on — what they
    had said beforehand is history. An absent row, or a row carrying a
    notice we never store (only HARD/SOFT ever reach ``Rsvp.notice``), reads
    as ``NONE`` — a walk-in.
    """
    if revoked:
        return RsvpState.REVOKED
    if notice == AttendanceNotice.RSVP_HARD:
        return RsvpState.HARD
    if notice == AttendanceNotice.RSVP_SOFT:
        return RsvpState.SOFT
    return RsvpState.NONE


async def state_for(player: AnniPlayer, event: AnniEvent) -> RsvpState:
    """:class:`RsvpState` for one (event, player) — the single-row twin of
    :func:`states_by_uuid`, for the write paths that need one player's state
    (``buckets.ensure_placed`` deciding whether an arrival is late).

    Unfiltered for the same reason: a revoked row is a state, not an absence.
    """
    row = await Rsvp.filter(event=event, player=player).first()
    if row is None:
        return RsvpState.NONE
    return state_of(row.notice, row.revoked_at is not None)


async def states_by_uuid(event: AnniEvent) -> dict[str, RsvpState]:
    """``{player_uuid: RsvpState}`` for every RSVP row on ``event``.

    Revoked rows are *included* (that is the whole point — they carry
    ``REVOKED``), so this is one unfiltered query rather than the
    ``revoked_at__isnull=True`` reads the rest of the module does. Players
    with no row simply aren't in the dict; callers default them to
    :attr:`RsvpState.NONE` (see :func:`state_of`).

    ``Rsvp.player`` FKs ``AnniPlayer.mc_uuid`` (the PK), so ``player_id`` is
    already the uuid — no join needed.
    """
    return {
        r.player_id: state_of(r.notice, r.revoked_at is not None)
        for r in await Rsvp.filter(event=event).only(
            "id", "player_id", "notice", "revoked_at"
        )
    }
