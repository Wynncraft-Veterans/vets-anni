"""Profile lifecycle — deleting an :class:`AnniPlayer` row that shouldn't exist.

Every other path in this app *creates* players and none deleted them, which is
fine right up until a typo does: ``\\rsvp set``, the staff board's "Add Players"
(``buckets.add_walkin``) and ``set_organizer`` all get-or-create an
:class:`AnniPlayer` for whatever IGN resolved, so a mis-typed-but-real
Minecraft name became a permanent ghost — on the board, in the roles
dashboard, in every host/organiser dropdown, with no way back out.

This module is the way back out, for **any** profile — not just the empty
shells it originally covered. Restricting it to shells sounded prudent and
wasn't: the profiles staff most need gone are the ones a typo has already
attached an RSVP or a capability to, and refusing those left the ghost
permanent while the button sat there greyed out.

So :func:`purge` always deletes, and :func:`holdings` moved from being a
*veto* to being a *warning*: it enumerates everything that makes a row
somebody's actual data — a dashboard login, declared capabilities, any RSVP
(revoked ones included; they're an audit trail), hosting a party, being an
event's lead organiser — and the caller puts that in the confirmation so
nobody destroys those unknowingly. A delete cascades
:class:`BoardPlacement`, :class:`RoleCapability` and :class:`Rsvp`; a party
host or lead organiser FK is ``SET_NULL``, so those survive the delete
minus a name.

Deleting a shell is recoverable by construction: the next walk-in add / RSVP
/ login recreates the row from the same resolver that created it in the
first place. (It can also come back on its own — the auto-promoter
re-materialises a placeholder for any guild member it sees online during the
hot window. Nothing to do about that here; it's the auto-promoter working as
specified.) Deleting a profile that held real data is NOT recoverable, which
is exactly why the warning exists.

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


async def holdings(player: AnniPlayer) -> list[str]:
    """Everything that makes ``player``'s profile real data, in staff-facing
    words. Empty list => the row is an empty shell.

    A warning, not a veto (see the module docstring): callers surface it in
    the delete confirmation. Ordered most-decisive first so a caller with
    room for one line shows the most consequential one.
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
    """Delete a profile outright, whatever it holds.

    The only refusal left is "no such profile" — everything else is the
    caller's to warn about (:func:`holdings`), because a staff member looking
    at a ghost row is better placed than this function to judge whether the
    RSVP hanging off it is real. Cascades placements, capabilities and RSVPs;
    nulls any party-host / lead-organiser FK.

    Returns the same :class:`~app.domain.buckets.OpResult` the board
    mutations use, so callers relay ``reason`` verbatim the way the hub
    does. On success the reason field carries a short "what happened" line —
    the only place a successful OpResult uses it — and it names what went
    with the profile, since by then it is the only record that it existed.
    """
    player = await AnniPlayer.filter(mc_uuid=player_uuid).first()
    if player is None:
        return OpResult(False, "That profile no longer exists.", player_uuid)

    name = player.mc_username
    held = await holdings(player)
    placements = await BoardPlacement.filter(player=player).count()
    await player.delete()  # cascades placements, capabilities and RSVPs
    logger.info(
        "purged profile: %s (%d placement(s); held: %s)",
        name, placements, "; ".join(held) or "nothing",
    )
    took = []
    if placements:
        took.append("took them off the board")
    if held:
        took.append(f"and destroyed what it held ({held[0]})")
    return OpResult(
        True,
        f"Deleted {name}'s profile" + (
            " — " + " ".join(took) + "." if took else "."
        ),
        player_uuid,
    )


async def holdings_by_uuid() -> dict[str, list[str]]:
    """:func:`holdings` for every player at once, as ``{uuid: [reason, ...]}``.

    The roles dashboard renders a row per player and needs each one's warning
    text; a per-row :func:`holdings` call would be five queries per player.
    Same predicate, expressed as bulk grouping: six constant-cost queries for
    the whole page. Players holding nothing are absent from the dict.

    Phrasing is duplicated from :func:`holdings` rather than shared, because
    the two answer slightly different questions — this one counts rows in
    bulk and never loads a player object. A test pins them to agree.
    """
    from collections import Counter

    out: dict[str, list[str]] = {}

    def add(uuid: str, phrase: str) -> None:
        out.setdefault(uuid, []).append(phrase)

    # Ordered to match holdings()'s most-decisive-first sequence.
    for uuid in await AnniPlayer.exclude(password_hash=None).values_list(
        "mc_uuid", flat=True
    ):
        add(uuid, "they have set a dashboard password")

    caps = Counter(await RoleCapability.all().values_list("player_id", flat=True))
    for uuid, n in caps.items():
        add(uuid, f"they have declared {n} role "
                  f"{'capability' if n == 1 else 'capabilities'}")

    rsvps = Counter(await Rsvp.all().values_list("player_id", flat=True))
    for uuid, n in rsvps.items():
        add(uuid, f"they have {n} RSVP{'' if n == 1 else 's'} on record")

    for host_id, ordinal in await Party.exclude(host=None).order_by(
        "ordinal"
    ).values_list("host_id", "ordinal"):
        if not any(r.startswith("they are the host") for r in out.get(host_id, [])):
            add(host_id, f"they are the host of Party {ordinal}")

    for uuid in await AnniEvent.exclude(organizer=None).values_list(
        "organizer_id", flat=True
    ):
        add(uuid, "they are an anni's lead organiser")

    return out
