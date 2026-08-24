"""Profile lifecycle — deleting an :class:`AnniPlayer` row that shouldn't exist.

Every other path in this app *creates* players and none deleted them, which is
fine right up until a typo does: ``\\rsvp set``, the staff board's "Add Players"
(``buckets.add_walkin``) and ``set_organizer`` all get-or-create an
:class:`AnniPlayer` for whatever IGN resolved, so a mis-typed-but-real
Minecraft name became a permanent ghost — on the board, in the roles
dashboard, in every host/organiser dropdown, with no way back out.

This module is the way back out, with one hard rule: **a purge may only ever
destroy an empty shell.** :func:`blockers` enumerates everything that makes a
row somebody's actual data — a dashboard login, declared capabilities, any
RSVP (revoked ones included; they're an audit trail), hosting a party, being
an event's lead organiser — and :func:`purge` refuses while any of them hold,
naming the one it found. What a purge *does* cascade is
:class:`BoardPlacement` rows, deliberately: taking the ghost off the board is
the point of deleting it, and by then we've proven the placement is the only
thing referencing it.

Deleting a shell is therefore recoverable by construction: the next
walk-in add / RSVP / login recreates the row from the same resolver that
created it in the first place. (It can also come back on its own — the
auto-promoter re-materialises a placeholder for any guild member it sees
online during the hot window. Nothing to do about that here; it's the
auto-promoter working as specified.)

Pure domain, ORM-only (mirrors ``domain/buckets``): no FastAPI, no discord.
"""

from __future__ import annotations

import logging

from app.db.models import (
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    Rsvp,
)
from app.domain.buckets import OpResult

logger = logging.getLogger("anni.players")


async def blockers(player: AnniPlayer) -> list[str]:
    """Every reason ``player``'s profile is real data, in staff-facing words.

    Empty list => the row is a shell that :func:`purge` may delete. Ordered
    most-decisive first so a caller that only shows one reason shows the
    most convincing one.
    """
    found: list[str] = []

    if player.password_hash:
        found.append("they have set a dashboard password")

    caps = await RoleCapability.filter(player=player).count()
    if caps:
        found.append(
            f"they have declared {caps} role "
            f"{'capability' if caps == 1 else 'capabilities'}"
        )

    # Any RSVP at all, including revoked ones and ones for past events: an
    # RSVP is a human saying "I'm coming", and the revoked rows are kept
    # deliberately as an audit trail (``db/models.Rsvp``).
    rsvps = await Rsvp.filter(player=player).count()
    if rsvps:
        found.append(
            f"they have {rsvps} RSVP{'' if rsvps == 1 else 's'} on record"
        )

    hosting = await Party.filter(host=player).order_by("ordinal").first()
    if hosting is not None:
        found.append(f"they are the host of Party {hosting.ordinal}")

    if await AnniEvent.filter(organizer=player).exists():
        found.append("they are an anni's lead organiser")

    return found


async def purge(player_uuid: str) -> OpResult:
    """Delete a profile that holds nothing, or explain why it can't be.

    Returns the same :class:`~app.domain.buckets.OpResult` the board
    mutations use, so callers relay ``reason`` verbatim the way the hub
    does. On success the reason field carries a short "what happened" line
    (including whether a board placement went with it) — the only place a
    successful OpResult uses it.
    """
    player = await AnniPlayer.filter(mc_uuid=player_uuid).first()
    if player is None:
        return OpResult(False, "That profile no longer exists.", player_uuid)

    held = await blockers(player)
    if held:
        return OpResult(
            False,
            f"Can't delete {player.mc_username}: {held[0]}. Remove them from "
            f"the board instead.",
            player_uuid,
        )

    name = player.mc_username
    placements = await BoardPlacement.filter(player=player).count()
    await player.delete()  # cascades the placement rows proven empty above
    logger.info("purged profile: %s (%d placement(s))", name, placements)
    return OpResult(
        True,
        (
            f"Deleted {name}'s profile"
            + (" and removed them from the board." if placements else ".")
        ),
        player_uuid,
    )


async def deletable_uuids() -> set[str]:
    """The uuids :func:`purge` would accept, resolved in one bulk pass.

    The roles dashboard renders a row per player and only offers Delete on
    shells, so it needs this answer for *every* player at once — a
    per-row :func:`blockers` call would be five queries per player. Same
    predicate, expressed as set subtraction: five constant-cost queries for
    the whole page.
    """
    all_uuids: set[str] = set(
        await AnniPlayer.all().values_list("mc_uuid", flat=True)
    )
    with_password: set[str] = set(
        await AnniPlayer.exclude(password_hash=None)
        .values_list("mc_uuid", flat=True)
    )
    with_caps: set[str] = set(
        await RoleCapability.all().values_list("player_id", flat=True)
    )
    with_rsvp: set[str] = set(
        await Rsvp.all().values_list("player_id", flat=True)
    )
    hosts: set[str] = set(
        await Party.exclude(host=None).values_list("host_id", flat=True)
    )
    organizers: set[str] = set(
        await AnniEvent.exclude(organizer=None)
        .values_list("organizer_id", flat=True)
    )
    return all_uuids - with_password - with_caps - with_rsvp - hosts - organizers
