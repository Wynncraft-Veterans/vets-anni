"""Profile purge — the escape hatch for a mistakenly-created AnniPlayer.

``buckets.add_walkin`` / ``set_organizer`` / ``\\rsvp set`` all get-or-create a
player row for whatever IGN resolved, so a typo that happens to be a real
Minecraft name becomes a permanent ghost. ``domain/players.purge`` deletes one,
under a hard rule these tests pin from both sides: a shell goes, and anything
holding *any* real user data is refused with the reason named.

The seed gives us one of each: ``baz`` (walk-in lane, no caps/RSVP/password),
``Wenweia`` (capabilities + RSVP), ``Nazzae`` (party 2's host), ``Holidaze``
(the event's lead organiser).
"""

from __future__ import annotations

from app.db.models import AnniPlayer, BoardPlacement, RoleCapability
from app.domain import players


async def test_purge_deletes_a_shell_and_its_board_card(seeded):
    event = seeded["event"]
    baz = seeded["players"]["baz"]
    assert await BoardPlacement.filter(event=event, player=baz).exists()

    r = await players.purge(baz.mc_uuid)

    assert r.ok
    assert "removed them from the board" in r.reason  # says what it took
    assert not await AnniPlayer.filter(mc_uuid=baz.mc_uuid).exists()
    # The placement cascaded with the row (it was the only thing pointing at it).
    assert not await BoardPlacement.filter(event=event, player_id=baz.mc_uuid).exists()


async def test_purge_refuses_a_player_with_capabilities_and_rsvps(seeded):
    wen = seeded["players"]["Wenweia"]

    r = await players.purge(wen.mc_uuid)

    assert not r.ok
    assert "capabilities" in r.reason           # the most decisive blocker first
    assert "Remove them from the board" in r.reason
    assert await AnniPlayer.filter(mc_uuid=wen.mc_uuid).exists()


async def test_purge_refuses_a_party_host_and_the_lead_organiser(seeded):
    """Holidaze is the seed's organiser *and* party 1's host, and has no
    capabilities/RSVP/password — so both blockers are provably the ones
    doing the work here (``purge`` reports only the first)."""
    holidaze = seeded["players"]["Holidaze"]

    held = await players.blockers(holidaze)
    assert held == ["they are the host of Party 1",
                    "they are an anni's lead organiser"]

    r = await players.purge(holidaze.mc_uuid)
    assert not r.ok and "host of Party 1" in r.reason
    assert await AnniPlayer.filter(mc_uuid=holidaze.mc_uuid).exists()


async def test_purge_refuses_a_dashboard_login(seeded):
    """A password means they registered themselves — never a mistaken add.

    ``Faulischlumpf`` is otherwise a shell (walk-in lane, no caps, no RSVP),
    so the password is provably the only thing doing the blocking.
    """
    fz = seeded["players"]["Faulischlumpf"]
    assert fz.mc_uuid in await players.deletable_uuids()

    fz.password_hash = "pbkdf2_sha256$whatever"
    await fz.save(update_fields=["password_hash"])

    r = await players.purge(fz.mc_uuid)
    assert not r.ok and "dashboard password" in r.reason
    assert await AnniPlayer.filter(mc_uuid=fz.mc_uuid).exists()


async def test_purge_refuses_a_revoked_rsvp(seeded):
    """Revoked RSVPs are kept as an audit trail (db/models.Rsvp) — they are
    still a human having said "I'm coming", so they block a purge."""
    from app.domain import rsvp as rsvp_domain

    event = seeded["event"]
    trix = seeded["players"]["Trixomaniac"]
    assert await rsvp_domain.revoke(trix, event) is not None

    r = await players.purge(trix.mc_uuid)
    assert not r.ok and "RSVP" in r.reason


async def test_purge_of_an_unknown_uuid_is_a_friendly_reject(seeded):
    r = await players.purge("00000000-0000-0000-0000-000000000000")
    assert not r.ok and "no longer exists" in r.reason


async def test_deletable_uuids_matches_purge_one_for_one(seeded):
    """The bulk predicate the roles dashboard renders from must agree with
    the per-player guard, or the UI offers buttons that then refuse."""
    deletable = await players.deletable_uuids()

    for player in await AnniPlayer.all():
        held = await players.blockers(player)
        assert (player.mc_uuid in deletable) is (not held), player.mc_username

    # Sanity on the fixture: it contains both kinds.
    assert seeded["players"]["baz"].mc_uuid in deletable
    assert seeded["players"]["Wenweia"].mc_uuid not in deletable


async def test_deleting_the_last_capability_makes_a_player_deletable(seeded):
    """The dashboard recomputes deletability per render, so a staff
    capability delete can hand back a row that now offers Delete profile."""
    para = seeded["players"]["Paradrex"]        # one TERTIARY cap, one RSVP
    from app.db.models import Rsvp
    await Rsvp.filter(player=para).delete()     # ...and no RSVP history

    assert para.mc_uuid not in await players.deletable_uuids()
    await RoleCapability.filter(player=para).delete()
    assert para.mc_uuid in await players.deletable_uuids()
