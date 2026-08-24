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
from app.constants import MAX_PARTY_STAGE, MIN_PARTY_STAGE, ROLE_SORT_PRIORITY
from app.db.models import AnniEvent, AnniPlayer, BoardPlacement, Party
from app.domain import identity, membership
from app.domain.identity import MojangResolver, mojang_username_to_uuid
from app.domain.presence import normalize_world
from app.services import hot_window
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


def _role_priority(role: Role | None) -> int:
    """Numeric priority for role-based default sort. Lower sorts earlier;
    unknown / off-table values fall to the tail (mirrors ``None``)."""
    return ROLE_SORT_PRIORITY.get(role, ROLE_SORT_PRIORITY[None])


async def _container_siblings(
    event: AnniEvent,
    *,
    bucket: BucketKind | None,
    party: Party | None,
    is_late: bool,
    is_walkin: bool,
    exclude_player: AnniPlayer | None = None,
) -> list[BoardPlacement]:
    """All placements in the destination container, ordered by current
    ``sort_index``. UNASSIGNED has three lanes (main / walkin / late) that
    render as distinct dropzones, so the filter narrows to the specific
    lane; VOLUNTEERS/WONTASSIGN/party targets ignore the lane flags."""
    query = BoardPlacement.filter(event=event)
    if party is not None:
        query = query.filter(party=party)
    else:
        query = query.filter(bucket=bucket, party=None)
        if bucket == BucketKind.UNASSIGNED:
            query = query.filter(is_late=is_late, is_walkin=is_walkin)
    if exclude_player is not None:
        query = query.exclude(player=exclude_player)
    return await query.order_by("sort_index")


async def _role_priority_slot(
    event: AnniEvent,
    *,
    bucket: BucketKind | None,
    party: Party | None,
    is_late: bool,
    is_walkin: bool,
    incoming_role: Role | None,
) -> int:
    """The position at which a card with ``incoming_role`` should slot into
    the container so the container stays sorted by role priority. Walks
    existing placements in current sort_index order and returns the index
    of the first one whose role priority is strictly greater than the
    incoming's; falls through to the tail if every existing card outranks
    the newcomer.

    Auto-place paths (RSVP promoter, walk-in add, revoke demote) call this
    with ``incoming_role=None``; drag-triggered ``move()`` passes an
    explicit position from the client instead."""
    existing = await _container_siblings(
        event, bucket=bucket, party=party,
        is_late=is_late, is_walkin=is_walkin,
    )
    incoming = _role_priority(incoming_role)
    for i, p in enumerate(existing):
        if _role_priority(p.assigned_role) > incoming:
            return i
    return len(existing)


async def _upsert(
    event: AnniEvent,
    player: AnniPlayer,
    *,
    bucket: BucketKind | None,
    party: Party | None,
    sort_index: int,
    is_late: bool | None,
    is_walkin: bool | None,
) -> BoardPlacement:
    """The single-instance UPSERT + destination-container renumber.

    One ``(event, player)`` row, always — a move is this row changing
    container, never a new row. Additionally: every other placement in the
    destination container is renumbered so ``sort_index`` values are
    contiguous ``0..N-1`` in the resulting order, with the moving row
    landing at position ``sort_index`` (clamped to ``0..N``).

    Without this renumber, dropping a card between two siblings collides
    with an existing ``sort_index`` and the render (which orders by
    ``sort_index`` with no stable tiebreaker) picks arbitrarily. The
    renumber is what makes drag-reorder within a bucket actually stick.

    Source-container gaps (from cross-container moves) are left alone —
    they're display-invisible (``.order_by("sort_index")`` handles gaps
    fine) and heal on the next insert into that container.

    Wrapped in a transaction so a mid-splice crash can't leave duplicated
    or gapped sort_indexes on the destination side.
    """
    async with in_transaction():
        placement = await BoardPlacement.filter(event=event, player=player).first()
        if placement is None:
            placement = BoardPlacement(event=event, player=player)
        placement.bucket = bucket
        placement.party = party
        if is_late is not None:
            placement.is_late = is_late
        elif placement.id is None:
            placement.is_late = False
        if is_walkin is not None:
            placement.is_walkin = is_walkin
        elif placement.id is None:
            placement.is_walkin = False

        siblings = await _container_siblings(
            event, bucket=bucket, party=party,
            is_late=placement.is_late, is_walkin=placement.is_walkin,
            exclude_player=player,
        )
        pos = max(0, min(sort_index, len(siblings)))
        ordered = siblings[:pos] + [placement] + siblings[pos:]
        for i, p in enumerate(ordered):
            # Always save the moving row (it may be brand-new, or its
            # bucket/party/lane changed even if the index is unchanged);
            # save siblings only when their sort_index actually shifts.
            if p is placement:
                p.sort_index = i
                await p.save()
            elif p.sort_index != i:
                p.sort_index = i
                await p.save(update_fields=["sort_index", "updated_at"])
    return placement


async def move(
    event: AnniEvent,
    player_uuid: str,
    *,
    bucket: BucketKind | None = None,
    party_id: str | None = None,
    sort_index: int = 0,
    is_late: bool | None = None,
    is_walkin: bool | None = None,
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
                  sort_index=sort_index, is_late=is_late,
                  is_walkin=is_walkin)
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


async def ensure_placed(
    event: AnniEvent,
    player: AnniPlayer,
    *,
    is_late: bool,
    is_walkin: bool = False,
) -> bool:
    """Idempotent auto-place: if ``(event, player)`` has no placement,
    insert one in the UNASSIGNED bucket and return ``True``; otherwise
    leave the existing row alone and return ``False``.

    Single-instance invariant: this is the shared "land them on the board"
    path for both the RSVP cog and the 1hr-early auto-promoter. The lane
    flags (``is_late``, ``is_walkin``) choose the sub-bucket at *insert
    time only* — already-placed players are never reshuffled between lanes
    by a subsequent tick; staff intent (or the original auto-place) wins.

    Caller contract: ``is_late`` and ``is_walkin`` are mutually exclusive in
    practice — the auto-promoter routes non-RSVP'd arrivals to walk-in
    before T-60 and to LATE after T-60, while RSVP'd arrivals always land
    in the main lane (both False). The view layer treats ``is_late`` as
    winning if both somehow ended up True.

    ``sort_index`` is derived from role-priority: an auto-placed card has
    no assigned role yet (``None`` → tail of the role-sorted lane), so it
    lands at the bottom of its dropzone. :func:`_upsert` also renumbers
    the lane so sort_indexes stay contiguous.
    """
    existing = await BoardPlacement.filter(event=event, player=player).first()
    if existing is not None:
        return False
    slot = await _role_priority_slot(
        event, bucket=BucketKind.UNASSIGNED, party=None,
        is_late=is_late, is_walkin=is_walkin, incoming_role=None,
    )
    await _upsert(
        event,
        player,
        bucket=BucketKind.UNASSIGNED,
        party=None,
        sort_index=slot,
        is_late=is_late,
        is_walkin=is_walkin,
    )
    if is_late:
        lane = "LATE"
    elif is_walkin:
        lane = "walk-in"
    else:
        lane = "main"
    logger.info("auto-place: %s -> Unassigned (%s)", player.mc_username, lane)
    return True


async def promote_from_wontassign(
    event: AnniEvent, player: AnniPlayer
) -> bool:
    """If the player's placement is in WONTASSIGN, move it back to the main
    UNASSIGNED lane and return ``True``; any other placement (party,
    UNASSIGNED, VOLUNTEERS) or no placement at all is a no-op (False).

    Called from :func:`_auto_place_after_rsvp` so a re-RSVP after a
    previous revoke promotes the player back into the placement queue
    instead of leaving them stranded in WONTASSIGN. The cog's
    "RSVP'd users always land in the main Unassigned lane" rule
    applies — even within the T-60 to T-90 window the lane is still
    main (``is_late=False``, ``is_walkin=False``).

    Yes, this overrides a staff-set WONTASSIGN if the staff move happens
    to predate the re-RSVP. That's acceptable: an explicit
    ``/wv anni rsvp`` is a strong user signal of intent. Staff can
    re-demote via the board if needed.
    """
    placement = await BoardPlacement.filter(event=event, player=player).first()
    if placement is None or placement.bucket is not BucketKind.WONTASSIGN:
        return False
    slot = await _role_priority_slot(
        event, bucket=BucketKind.UNASSIGNED, party=None,
        is_late=False, is_walkin=False,
        incoming_role=placement.assigned_role,
    )
    await _upsert(
        event,
        player,
        bucket=BucketKind.UNASSIGNED,
        party=None,
        sort_index=slot,
        is_late=False,
        is_walkin=False,
    )
    logger.info(
        "rsvp re-promote: %s -> Unassigned (main lane)", player.mc_username
    )
    return True


async def demote_on_revoke(event: AnniEvent, player: AnniPlayer) -> bool:
    """If the player's placement is in the UNASSIGNED bucket (either lane),
    move it to WONTASSIGN and return ``True``. Any other placement (party,
    WONTASSIGN, VOLUNTEERS) or no placement at all is a no-op (False) — staff
    intent wins, and the "Retracted" pill (driven by ``Rsvp.revoked_at`` in
    the view layer) surfaces the retraction regardless of where the card
    physically sits.
    """
    placement = await BoardPlacement.filter(event=event, player=player).first()
    if placement is None or placement.bucket is not BucketKind.UNASSIGNED:
        return False
    slot = await _role_priority_slot(
        event, bucket=BucketKind.WONTASSIGN, party=None,
        is_late=False, is_walkin=False,
        incoming_role=placement.assigned_role,
    )
    await _upsert(
        event,
        player,
        bucket=BucketKind.WONTASSIGN,
        party=None,
        sort_index=slot,
        is_late=False,
        is_walkin=False,
    )
    logger.info("rsvp revoke demote: %s -> Wont-assign", player.mc_username)
    return True


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
        # A staff walk-in routes through ``identity.resolve_identity`` — it's
        # the canonical "this is a real person, here's their data" path, so
        # we clear the auto-promoter placeholder flag if it was set.
        player.is_placeholder = False
        await player.save(update_fields=[
            "mc_username", "wynn_username", "guild", "membership_tier",
            "is_placeholder", "updated_at",
        ])

    existing = await BoardPlacement.filter(event=event, player=player).first()
    if existing is not None:
        # Single-instance: already placed -> no-op (do NOT yank them back to
        # Unassigned, do NOT create a second row).
        logger.debug("walk-in %s already on board — no-op", player.mc_username)
        return OpResult(True, player_uuid=player.mc_uuid)

    # A staff "add by IGN" is the manual walk-in path — mirror the auto-
    # promoter's lane logic: walk-in sub-bucket before T-60, LATE after.
    late = hot_window.is_late_bucket(event)
    walkin = not late
    slot = await _role_priority_slot(
        event, bucket=BucketKind.UNASSIGNED, party=None,
        is_late=late, is_walkin=walkin, incoming_role=None,
    )
    await _upsert(event, player, bucket=BucketKind.UNASSIGNED, party=None,
                  sort_index=slot, is_late=late, is_walkin=walkin)
    logger.info(
        "walk-in added: %s -> Unassigned (%s)",
        player.mc_username, "LATE" if late else "walk-in",
    )
    return OpResult(True, player_uuid=player.mc_uuid)


async def remove_player(event: AnniEvent, player_uuid: str) -> OpResult:
    """Take one person off the board — the inverse of :func:`add_walkin`.

    Deletes the ``(event, player)`` :class:`BoardPlacement` and nothing else:
    the :class:`AnniPlayer` row, their RSVP and their capabilities all
    survive, because "shouldn't be on tonight's board" is not "isn't a
    person". Deleting the *profile* of a mistaken add is a separate,
    guarded operation (:mod:`app.domain.players`).

    Refuses while the player hosts a party, mirroring :func:`delete_party`'s
    refusal to delete a non-empty one: ``Party.host`` is a player FK, not a
    placement FK, so removing them would leave a host who isn't on the board
    (rendering as "— none —" in the host select while the collapsed party
    head still names them). Staff change the host first; we never silently
    rewrite a party as a side effect of a different action.

    Not idempotent-friendly by design — removing someone who isn't placed is
    a friendly reject, so a double-click on a stale card says so rather than
    reporting success for a no-op.

    Note for the caller: during the hot window the auto-promoter re-adds
    anyone it sees online in the merge cache (``services/auto_promoter``), so
    removing an online guild member is temporary by nature.
    """
    placement = await (
        BoardPlacement.filter(event=event, player__mc_uuid=player_uuid)
        .select_related("player")
        .first()
    )
    if placement is None:
        return OpResult(False, "That player isn't on the board.", player_uuid)

    hosting = await Party.filter(event=event, host__mc_uuid=player_uuid).first()
    if hosting is not None:
        return OpResult(
            False,
            f"They're hosting Party {hosting.ordinal} — set that party's "
            f"host to someone else first.",
            player_uuid,
        )

    name = placement.player.mc_username
    await placement.delete()
    logger.info("removed from board: %s", name)
    return OpResult(True, player_uuid=player_uuid)


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


async def delete_party(event: AnniEvent, party_id: str) -> OpResult:
    """Remove an *empty* party. Non-empty is a friendly REJECTED so members
    aren't silently stranded (BoardPlacement.party is SET_NULL on delete,
    which would violate the exactly-one-of-bucket-or-party invariant)."""
    party = await Party.filter(id=party_id, event=event).first()
    if party is None:
        return OpResult(False, "That party no longer exists.")
    if await BoardPlacement.filter(event=event, party=party).exists():
        return OpResult(False, "Move its members out before deleting.")
    ordinal = party.ordinal
    await party.delete()
    logger.info("party deleted: #%d", ordinal)
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
        party.world = normalize_world(world)
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
    columns render (container then ``sort_index``).

    ``capabilities`` is the player's declared :class:`RoleCapability` rows with
    weapons eager-loaded — surfaced on the person card as small role-coloured
    dots (what a player *can* do, distinct from ``assigned_role`` which is
    what they *will* do in this anni). Raw rows only; the popover shape lives
    in ``app/web/board_view``."""
    rows = (
        await BoardPlacement.filter(event=event)
        .select_related("player", "party")
        .prefetch_related("player__capabilities__weapons")
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
                "is_placeholder": r.player.is_placeholder,
                "bucket": r.bucket.value if r.bucket else None,
                "party_id": str(r.party.id) if r.party else None,
                "party_ordinal": r.party.ordinal if r.party else None,
                "assigned_role": r.assigned_role,
                "is_late": r.is_late,
                "is_walkin": r.is_walkin,
                "sort_index": r.sort_index,
                "capabilities": list(r.player.capabilities),
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
