"""Canonical per-player anni snapshot — the single shape vetsmod consumes.

The MWE (Major World Event) integration has ONE wire shape: the dict produced
by :func:`assemble_snapshot`. Both the push stream (temp-server polls vets-anni
and pushes per-uuid changes) and the on-demand pull (vetsmod issues an
``anni_query`` over WS, temp-server hits ``/api/internal/anni-player/{uuid}``)
return identical objects. vetsmod treats them as opaque transit and renders
verbatim — meaning every breaking shape change is a schema_version bump here.

See ``.claude/snapshot_integration.md`` for the JSON schema documentation and
the upgrade-coordination story.

Pure-ish: takes an :class:`AnniPlayer` + :class:`AnniEvent` (the caller looked
those up) plus the shared :class:`AppState` for the live presence/online map.
Issues only the reads required to fill the shape. Returns plain dicts (no
Pydantic) — the consumer is a JSON pipe, not a typed Python caller.
"""

from __future__ import annotations

import logging
from typing import Any

from app.constants import (
    BUCKET_LABEL,
    DOCS_BASE,
    ROLE_GUIDANCE,
    BucketKind,
    MembershipTier,
    PartyResult,
    Role,
)
from app.db.lifecycle import get_active_event
from app.db.models import (
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    Party,
    Rsvp,
    RoleCapability,
)
from app.domain import attendance as attendance_domain
from app.domain import rsvp as rsvp_domain
from app.domain.anni_prediction import predict_next
from app.services.state import AppState

logger = logging.getLogger("anni.domain.snapshot")

SCHEMA_VERSION = 1

#: Tiers the snapshot push stream considers "plausible vets-anni users". Any
#: player with a tier in this set OR with an existing :class:`AnniPlayer` row
#: (regardless of tier) is eligible. The set is exported so the eligibility
#: endpoint can use the same vocabulary.
PUSH_ELIGIBLE_TIERS: frozenset[MembershipTier] = frozenset(
    {
        MembershipTier.MEMBER,
        MembershipTier.WAITLIST,
        MembershipTier.HONOURARY,
    }
)


# ---------------------------------------------------------------------------
# Helpers — every sub-object of the snapshot has a small builder. Each one is
# pure-ish (takes the resolved domain objects) so unit tests can hit them
# without spinning up the whole DB.
# ---------------------------------------------------------------------------
def _to_epoch(dt: Any) -> int | None:
    if dt is None:
        return None
    try:
        return int(dt.timestamp())
    except (AttributeError, TypeError, ValueError):
        return None


async def _build_event_block(event: AnniEvent | None) -> dict | None:
    """``event`` sub-object. ``None`` iff no AnniEvent has ever been created.

    Adds ``prediction`` whenever the stamp is past/unknown so external users
    invoking ``/wv anni`` see the ``\\guess``-style window instead of bare
    "not announced" text (per S2).
    """
    if event is None:
        return None
    stamp_epoch = int(event.stamp_epoch) if event.stamp_epoch else None
    import time as _time

    now = int(_time.time())
    needs_prediction = stamp_epoch is None or stamp_epoch <= now

    prediction: dict | None = None
    if needs_prediction:
        anchor = stamp_epoch
        if anchor is None:
            # Fallback: use the most recent past anni stamp in the DB. Returns
            # None when the DB has never seen one (cold start). Snapshot
            # consumers tolerate ``prediction: null`` and fall back to the
            # legacy "not announced" string.
            past = (
                await AnniEvent.filter(stamp_epoch__lte=now)
                .order_by("-stamp_epoch")
                .first()
            )
            if past is not None and past.stamp_epoch:
                anchor = int(past.stamp_epoch)
        if anchor is not None:
            prediction = predict_next(anchor)

    return {
        "stamp_epoch": stamp_epoch if stamp_epoch and stamp_epoch > now else None,
        "announced": stamp_epoch is not None and stamp_epoch > now,
        "prediction": prediction,
    }


async def _build_registration_block(player: AnniPlayer) -> dict:
    """``registration``: whether the player has any RoleCapability rows + the
    titles/urls per role. ``core`` is True iff the player has at least one
    capability row (so they can be assigned a core role on the board).
    """
    rows = await RoleCapability.filter(player=player).all()
    seen: set[str] = set()
    roles_out: list[dict] = []
    for row in rows:
        role = row.role
        # ``role`` comes back as the enum value (string) thanks to CharEnumField.
        key = str(role)
        if key in seen:
            continue
        seen.add(key)
        guidance = ROLE_GUIDANCE.get(Role(key)) if isinstance(role, str) else None
        if guidance is not None:
            roles_out.append(
                {
                    "role": Role(key).name,
                    "title": guidance.title,
                    "url": guidance.gameplay_url,
                }
            )
        else:
            # Unknown role enum (forward-compat); pass through without URL.
            roles_out.append(
                {
                    "role": str(role).upper(),
                    "title": str(role).title(),
                    "url": f"{DOCS_BASE}/",
                }
            )
    return {
        "registered": bool(roles_out),
        "core": bool(roles_out),
        "roles": roles_out,
    }


async def _build_rsvp_block(player: AnniPlayer, event: AnniEvent) -> dict | None:
    """``rsvp``: the active (non-revoked) RSVP, or ``None``."""
    row = await rsvp_domain.get_current(player, event)
    if row is None:
        return None
    return {
        "notice": "hard" if row.notice.value == "rsvp_hard" else "soft",
        "updated_at": _to_epoch(row.updated_at),
        "revoked": False,
    }


def _player_brief(player: AnniPlayer | None) -> dict | None:
    if player is None:
        return None
    return {"uuid": player.mc_uuid, "username": player.mc_username}


async def _build_party_block(party: Party, event: AnniEvent) -> dict:
    """Render a :class:`Party` plus its membership for the snapshot. Includes
    the in-party ``role`` per member (used by S4 outlines: distinguishing
    same-party teammates from other-vets-party players)."""
    members_raw = (
        await BoardPlacement.filter(party=party, event=event)
        .select_related("player")
        .all()
    )
    members = [
        {
            "uuid": p.player.mc_uuid,
            "username": p.player.mc_username,
            "role": (p.assigned_role.name if p.assigned_role else Role.FILL.name),
        }
        for p in members_raw
    ]
    return {
        "ordinal": party.ordinal,
        "world": party.world,
        "result": (
            party.result.value if party.result != PartyResult.TBD else None
        ),
        "host": _player_brief(party.host) if party.host_id else None,
        "members": members,
    }


async def _build_board_block(player: AnniPlayer, event: AnniEvent) -> dict:
    """``board``: where this player sits on the staff board.

    State values mirror the spec: ``unplaced`` (no row), ``wont_assign``
    (bucket=WONTASSIGN), ``unassigned`` (bucket=UNASSIGNED|VOLUNTEERS), or
    ``party`` (placed in a Party). Carries the party sub-object when the
    player is in a party.
    """
    placement = (
        await BoardPlacement.filter(event=event, player=player)
        .select_related("party", "party__host")
        .first()
    )
    if placement is None:
        return {
            "state": "unplaced",
            "party": None,
            "role": None,
            "wont_reason": None,
        }

    if placement.party is not None:
        party_block = await _build_party_block(placement.party, event)
        return {
            "state": "party",
            "party": party_block,
            "role": (
                placement.assigned_role.name
                if placement.assigned_role
                else None
            ),
            "wont_reason": None,
        }

    bucket = placement.bucket
    if bucket == BucketKind.WONTASSIGN:
        return {
            "state": "wont_assign",
            "party": None,
            "role": None,
            "wont_reason": BUCKET_LABEL.get(BucketKind.WONTASSIGN),
        }
    # UNASSIGNED + VOLUNTEERS both surface as "unassigned" to vetsmod —
    # vetsmod doesn't differentiate (the differentiation matters for the
    # staff board, not for the in-game enriched view).
    return {
        "state": "unassigned",
        "party": None,
        "role": None,
        "wont_reason": None,
    }


def _build_attendance_block(
    player: AnniPlayer,
    rsvp_row: Rsvp | None,
    event: AnniEvent | None,
    *,
    in_party: bool,
) -> dict:
    """``attendance``: band index + label + the effective notice.

    Mirrors the dashboard's General-module bottom bar so the in-game text
    and the web view agree. ``in_party`` selects the Core vs Fill row of
    the published table (anyone placed in a party counts as Core for the
    purpose of attendance likelihood — they have a slot).
    """
    tier = player.membership_tier
    notice_stored = rsvp_row.notice if rsvp_row is not None else None

    if event is None or not event.stamp_epoch:
        seconds_to_anni: int | None = None
    else:
        import time as _time

        seconds_to_anni = max(0, int(event.stamp_epoch) - int(_time.time()))

    effective = attendance_domain.effective_notice(
        notice_stored, seconds_to_anni, tier=tier
    )
    pct = attendance_domain.evaluate(tier, core=in_party, notice=effective)
    band, label = attendance_domain.meta(pct)
    return {
        "band": band,
        "label": label,
        "notice_effective": (effective.value if effective is not None else None),
    }


async def _organiser_uuids(event: AnniEvent) -> list[str]:
    """UUIDs of every organising role for this event: the lead organiser
    plus every Party host. De-duplicated; stable order (lead first, then
    party hosts in ordinal order). S7 gates the party-back-report pipeline
    on this list."""
    out: list[str] = []
    seen: set[str] = set()
    if event.organizer_id:
        # ``organizer`` was eager-loaded by ``get_active_event``; mc_uuid IS
        # the FK key.
        uuid = (
            event.organizer.mc_uuid
            if event.organizer is not None
            else str(event.organizer_id)
        )
        if uuid and uuid not in seen:
            out.append(uuid)
            seen.add(uuid)
    parties = (
        await Party.filter(event=event)
        .select_related("host")
        .order_by("ordinal")
    )
    for p in parties:
        if p.host_id is None:
            continue
        uuid = p.host.mc_uuid if p.host is not None else str(p.host_id)
        if uuid and uuid not in seen:
            out.append(uuid)
            seen.add(uuid)
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------
async def assemble_snapshot(
    player: AnniPlayer,
    event: AnniEvent | None,
    state: AppState,
) -> dict:
    """Build the canonical snapshot dict for one player.

    ``event`` may be ``None`` (no announced anni): the snapshot still resolves
    (registration + tier are stamp-independent) — just with most domain blocks
    as their empty defaults.
    """
    registration = await _build_registration_block(player)

    if event is None:
        rsvp_block = None
        board_block = {
            "state": "unplaced",
            "party": None,
            "role": None,
            "wont_reason": None,
        }
        rsvp_row = None
        organisers: list[str] = []
    else:
        rsvp_row = await rsvp_domain.get_current(player, event)
        rsvp_block = (
            {
                "notice": (
                    "hard" if rsvp_row.notice.value == "rsvp_hard" else "soft"
                ),
                "updated_at": _to_epoch(rsvp_row.updated_at),
                "revoked": False,
            }
            if rsvp_row is not None
            else None
        )
        board_block = await _build_board_block(player, event)
        organisers = await _organiser_uuids(event)

    attendance = _build_attendance_block(
        player,
        rsvp_row,
        event,
        in_party=(board_block["state"] == "party"),
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "mc_uuid": player.mc_uuid,
        "mc_username": player.mc_username,
        "event": await _build_event_block(event),
        "registration": registration,
        "rsvp": rsvp_block,
        "board": board_block,
        "attendance": attendance,
        "organisers": organisers,
    }


async def assemble_snapshot_for_uuid(
    mc_uuid: str, state: AppState
) -> dict | None:
    """Convenience wrapper: resolve the player by UUID and snapshot them.

    ``None`` => no :class:`AnniPlayer` row for that UUID. Callers turn that into
    a 404; pulling the snapshot for a player who is not in the DB at all is
    out of scope (the DB-row IS the registration check).
    """
    player = await AnniPlayer.filter(mc_uuid=mc_uuid).first()
    if player is None:
        return None
    event = await get_active_event()
    return await assemble_snapshot(player, event, state)


async def is_push_eligible(player: AnniPlayer) -> bool:
    """Whether the push stream should be opened for this player.

    Per Hard Rule #3: tier ∈ {member, waitlist, honourary} OR any
    :class:`AnniPlayer` row at all (presence in the DB IS the "vets-anni knows
    about them" signal). Since we received ``player`` already, the only
    additional gate is to confirm the player object exists — which it does
    by construction. So this is effectively a no-op gate today, kept for
    future tier-specific filtering (e.g. if we ever want to *exclude* OTHER
    tier even when the row exists for some other reason).
    """
    del player  # noqa: F841 - kept for future filtering hooks
    return True


async def push_eligible_uuids() -> list[str]:
    """Every UUID the snapshot poller should fetch on each tick.

    All :class:`AnniPlayer` UUIDs (per Hard Rule #3: "if vets-anni knows about
    the player at all, they're a plausible vets-anni user"). Sorted for stable
    diffing on the temp-server side.
    """
    uuids = await AnniPlayer.all().values_list("mc_uuid", flat=True)
    return sorted({str(u) for u in uuids})
