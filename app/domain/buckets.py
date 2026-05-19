"""Board mutation path — the single-instance-per-person invariant in code.

Every change an organiser makes to the board funnels through here. It is the
*one* place ``BoardPlacement`` is written, and it always writes it as an
**UPSERT of the unique ``(event, player)`` row inside a transaction** — never
insert-then-delete — so a person can physically never be duplicated across
buckets/parties (the spec's hard "at most one instance of everyone" rule;
``.claude/data_model.md`` layer 2, ``unique_together`` is layer 1, and
``board_hub``'s sequential single-writer loop is layer 3).

Not FastAPI/discord aware (mirrors ``domain/identity`` — it touches the ORM
because it *is* the mutation rule, but stays out of the web/bot layers so it
stays unit-testable and reusable by the REST fallbacks + the WS hub alike).
Read-shaping for the wire/template lives in ``app/web/board_view`` so this
module is purely "apply a validated intent".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tortoise.transactions import in_transaction

from app.constants import ASSIGNABLE_ROLES, BucketKind, PartyResult, Role
from app.constants import MAX_PARTY_STAGE, MIN_PARTY_STAGE
from app.db.models import AnniEvent, AnniPlayer, BoardPlacement, Party
from app.domain import identity, membership
from app.domain.identity import MojangResolver, mojang_username_to_uuid
from app.services.state import AppState
from app.settings import get_settings

logger = logging.getLogger("anni.buckets")


@dataclass(frozen=True)
class OpResult:
    """Outcome of a board mutation. ``ok=False`` carries a *friendly* reason
    that the WS layer relays verbatim in a ``REJECTED`` frame (and the REST
    twin shows inline), so the cause is always legible to the organiser."""

    ok: bool
    reason: str | None = None
    player_uuid: str | None = None


def _one_container(bucket: BucketKind | None, party: Party | None) -> bool:
    """Exactly one of (bucket, party) must be set — the BoardPlacement shape
    invariant (``.claude/data_model.md``). Belt-and-braces over the DB column
    nullability so a bad intent is rejected, not silently half-applied."""
    return (bucket is None) != (party is None)


async def _upsert(
    event: AnniEvent,
    player: AnniPlayer,
    *,
    bucket: BucketKind | None,
    party: Party | None,
    sort_index: int,
    is_late: bool | None,
) -> BoardPlacement:
    """The single-instance UPSERT. One ``(event, player)`` row, always — a
    move is this row changing container, never a new row. Wrapped in a
    transaction so the unique constraint + the SQLite single writer make a
    duplicate impossible even under concurrent intents."""
    async with in_transaction():
        placement = await BoardPlacement.filter(event=event, player=player).first()
        if placement is None:
            placement = BoardPlacement(event=event, player=player)
        placement.bucket = bucket
        placement.party = party
        placement.sort_index = sort_index
        if is_late is not None:
            placement.is_late = is_late
        elif placement.id is None:
            placement.is_late = False
        await placement.save()
    return placement


async def move(
    event: AnniEvent,
    player_uuid: str,
    *,
    bucket: BucketKind | None = None,
    party_id: str | None = None,
    sort_index: int = 0,
    is_late: bool | None = None,
) -> OpResult:
    """Move a *board* player to a bucket or a party slot (UPSERT).

    The player must already exist (a real move only ever targets someone the
    organiser can see); adding a brand-new person is :func:`add_walkin`. An
    unknown player / party, or an ambiguous target, is ``REJECTED`` with a
    reason rather than guessed at.
    """
    player = await AnniPlayer.filter(mc_uuid=player_uuid).first()
    if player is None:
        return OpResult(False, "That player is no longer known.", player_uuid)

    party: Party | None = None
    if party_id is not None:
        party = await Party.filter(id=party_id, event=event).first()
        if party is None:
            return OpResult(False, "That party no longer exists.", player_uuid)
        bucket = None
    if not _one_container(bucket, party):
        return OpResult(False, "A player must land in exactly one place.",
                        player_uuid)

    await _upsert(event, player, bucket=bucket, party=party,
                  sort_index=sort_index, is_late=is_late)
    logger.debug("move %s -> %s", player.mc_username,
                 f"party {party.ordinal}" if party else bucket)
    return OpResult(True, player_uuid=player_uuid)


async def assign_role(
    event: AnniEvent, player_uuid: str, role: Role | None
) -> OpResult:
    """Set/clear a board player's assigned role (``None`` => grey unassigned).

    Only the assignable set (5 core + FILL) is accepted; capability rows still
    use the 5 core roles, FILL is colour/assign-only (``constants``).
    """
    if role is not None and role not in ASSIGNABLE_ROLES:
        return OpResult(False, "Not an assignable role.", player_uuid)
    placement = await (
        BoardPlacement.filter(event=event, player__mc_uuid=player_uuid)
        .select_related("player")
        .first()
    )
    if placement is None:
        return OpResult(False, "That player isn't on the board.", player_uuid)
    placement.assigned_role = role
    await placement.save(update_fields=["assigned_role", "updated_at"])
    logger.debug("assign_role %s -> %s",
                 placement.player.mc_username, role.value if role else "—")
    return OpResult(True, player_uuid=player_uuid)


async def add_walkin(
    event: AnniEvent,
    ign: str,
    state: AppState,
    *,
    mojang: MojangResolver = mojang_username_to_uuid,
) -> OpResult:
    """Staff "add user by IGN" — drop an arbitrary person into Unassigned.

    Resolves IGN→UUID via the cache-first resolver (``domain/identity`` →
    ``services/mojang``; **never** ``api.mojang.com``), get-or-creates the
    :class:`AnniPlayer` (so a walk-in who never RSVP'd / isn't in ``/wv list``
    still works), then UPSERTs the (event, player) row.

    **Idempotent by the single-instance rule:** a person already on the board
    is returned unchanged — re-adding them is a no-op, *never* a move back to
    Unassigned and never a duplicate. Unknown/unresolvable IGN → a friendly
    ``REJECTED`` reason.
    """
    ign = (ign or "").strip()
    if not ign:
        return OpResult(False, "Enter an in-game name.")

    ident = await identity.resolve_identity(ign, state, mojang=mojang)
    if ident is None:
        return OpResult(
            False,
            f"Couldn't find a Minecraft account for “{ign}”. Check the "
            "spelling (it's their IGN, not their Discord name).",
        )

    settings = get_settings()
    tier = membership.resolve(
        in_returners_roster=ident.in_returners_roster,
        dazebot_tier=None,
        guild_name=ident.guild_name,
        guild_tag=ident.guild_tag,
        ally_tags=settings.ally_guild_tag_set,
        returners_guild_name=settings.returners_guild_name,
    )
    player, created = await AnniPlayer.get_or_create(
        mc_uuid=ident.mc_uuid,
        defaults={
            "mc_username": ident.mc_username,
            "wynn_username": ident.wynn_username,
            "guild": ident.guild_name,
            "membership_tier": tier,
            "last_online": ident.last_online,
        },
    )
    if not created:
        # Keep the resolved-name/guild cache fresh even on a re-add attempt.
        player.mc_username = ident.mc_username
        player.wynn_username = ident.wynn_username
        player.guild = ident.guild_name
        player.membership_tier = tier
        await player.save(update_fields=[
            "mc_username", "wynn_username", "guild", "membership_tier",
            "updated_at",
        ])

    existing = await BoardPlacement.filter(event=event, player=player).first()
    if existing is not None:
        # Single-instance: already placed -> no-op (do NOT yank them back to
        # Unassigned, do NOT create a second row).
        logger.debug("walk-in %s already on board — no-op", player.mc_username)
        return OpResult(True, player_uuid=player.mc_uuid)

    tail = (
        await BoardPlacement.filter(event=event, bucket=BucketKind.UNASSIGNED)
        .count()
    )
    await _upsert(event, player, bucket=BucketKind.UNASSIGNED, party=None,
                  sort_index=tail, is_late=False)
    logger.info("walk-in added: %s -> Unassigned", player.mc_username)
    return OpResult(True, player_uuid=player.mc_uuid)


# --- parties ---------------------------------------------------------------
async def create_party(event: AnniEvent) -> Party:
    """Append a new party with the next free ordinal (unique per event)."""
    last = await Party.filter(event=event).order_by("-ordinal").first()
    ordinal = (last.ordinal + 1) if last else 1
    party = await Party.create(event=event, ordinal=ordinal)
    logger.info("party created: #%d", ordinal)
    return party


async def rename_party(
    event: AnniEvent, party_id: str, ordinal: int
) -> OpResult:
    """Renumber a party. Ordinals stay unique per event (rejected on clash)."""
    party = await Party.filter(id=party_id, event=event).first()
    if party is None:
        return OpResult(False, "That party no longer exists.")
    if ordinal < 1:
        return OpResult(False, "Party number must be positive.")
    clash = await Party.filter(event=event, ordinal=ordinal).exclude(
        id=party.id
    ).exists()
    if clash:
        return OpResult(False, f"Party {ordinal} already exists.")
    party.ordinal = ordinal
    await party.save(update_fields=["ordinal", "updated_at"])
    return OpResult(True)


async def set_party(
    event: AnniEvent,
    party_id: str,
    *,
    host_uuid: str | None = ...,   # ... = "not supplied"; None = clear host
    world: str | None = ...,
    stage: int | None = ...,
    result: str | None = ...,
) -> OpResult:
    """Patch a party's host/world/stage/result. Only supplied fields change
    (sentinel ``...`` = untouched) so the grace-phase "result/stage only"
    rule can call this with just those two. ``stage`` is clamped 1..5."""
    party = await Party.filter(id=party_id, event=event).first()
    if party is None:
        return OpResult(False, "That party no longer exists.")
    fields: list[str] = []

    if host_uuid is not ...:
        if host_uuid is None:
            party.host = None
        else:
            host = await AnniPlayer.filter(mc_uuid=host_uuid).first()
            if host is None:
                return OpResult(False, "That host isn't a known player.")
            party.host = host
        fields.append("host_id")
    if world is not ...:
        party.world = (world or "").strip() or None
        fields.append("world")
    if stage is not ...:
        party.stage = max(MIN_PARTY_STAGE, min(MAX_PARTY_STAGE, int(stage)))
        fields.append("stage")
    if result is not ...:
        try:
            party.result = PartyResult(str(result).strip().lower())
        except ValueError:
            return OpResult(False, "Unknown party result.")
        fields.append("result")

    if fields:
        await party.save(update_fields=[*fields, "updated_at"])
        logger.debug("party #%d set %s", party.ordinal, ",".join(fields))
    return OpResult(True)


async def set_organizer(
    event: AnniEvent, player_uuid: str | None, *, name: str | None = None
) -> OpResult:
    """Claim/release the lead-organiser slot. ``None`` releases it.

    Organiser candidates are now the full WAPI guild-staff list, most of whom
    have never logged into vets-anni — so when ``player_uuid`` has no
    :class:`AnniPlayer` yet, get-or-create a minimal one from ``name`` (the
    cached guild-staff username). With neither a row nor a name we still
    reject (an unknown uuid from a hand-crafted request).
    """
    if player_uuid is None:
        event.organizer = None
        await event.save(update_fields=["organizer_id"])
        return OpResult(True)
    player = await AnniPlayer.filter(mc_uuid=player_uuid).first()
    if player is None:
        if not name:
            return OpResult(False, "That organiser isn't a known player.")
        player, _ = await AnniPlayer.get_or_create(
            mc_uuid=player_uuid, defaults={"mc_username": name}
        )
    event.organizer = player
    await event.save(update_fields=["organizer_id"])
    logger.info("organiser set: %s", player.mc_username)
    return OpResult(True, player_uuid=player_uuid)


# --- read shaping (raw rows; presentation lives in web/board_view) ---------
async def board_rows(event: AnniEvent) -> list[dict]:
    """Every placement for ``event`` as plain dicts (no colour/avatar — that
    is ``app/web/board_view``'s job). One query, FK-eager, ordered the way the
    columns render (container then ``sort_index``)."""
    rows = (
        await BoardPlacement.filter(event=event)
        .select_related("player", "party")
        .order_by("sort_index")
    )
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "uuid": r.player.mc_uuid,
                "mc_username": r.player.mc_username,
                "wynn_username": r.player.wynn_username,
                "desynced": bool(
                    r.player.wynn_username
                    and r.player.wynn_username != r.player.mc_username
                ),
                "tier": r.player.membership_tier,
                "preferred_regions": r.player.preferred_regions,
                "last_online": r.player.last_online,
                "bucket": r.bucket.value if r.bucket else None,
                "party_id": str(r.party.id) if r.party else None,
                "party_ordinal": r.party.ordinal if r.party else None,
                "assigned_role": r.assigned_role,
                "is_late": r.is_late,
                "sort_index": r.sort_index,
            }
        )
    return out


async def parties_of(event: AnniEvent) -> list[Party]:
    """The event's parties, host eager-loaded, in display order."""
    return (
        await Party.filter(event=event)
        .select_related("host")
        .order_by("ordinal")
    )
