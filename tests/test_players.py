"""Profile purge — the escape hatch for a mistakenly-created AnniPlayer.

``buckets.add_walkin`` / ``set_organizer`` / ``\\rsvp set`` all get-or-create a
player row for whatever IGN resolved, so a typo that happens to be a real
Minecraft name becomes a permanent ghost. ``domain/players.purge`` deletes one
— **any** one. It used to refuse anything holding real user data, which read
as prudent and wasn't: the ghosts staff most need gone are exactly the ones a
typo has already attached an RSVP to.

So what these tests pin is the pair that replaced the veto: :func:`purge`
always deletes and reports what went with the row, while :func:`holdings` /
:func:`holdings_by_uuid` describe what that will be, in the words the
confirmation dialog shows. The bulk and per-player forms must agree, or the
dialog warns about something different from what happens.

The seed gives us one of each: ``baz`` (walk-in lane, no caps/RSVP/password),
``Wenweia`` (capabilities + RSVP), ``Nazzae`` (party 2's host), ``Holidaze``
(the event's lead organiser).
"""

from __future__ import annotations

from app.db.models import (
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    Rsvp,
)
from app.domain import players


async def test_purge_deletes_a_shell_and_its_board_card(seeded):
    event = seeded["event"]
    baz = seeded["players"]["baz"]
    assert await BoardPlacement.filter(event=event, player=baz).exists()

    r = await players.purge(baz.mc_uuid)

    assert r.ok
    assert "took them off the board" in r.reason  # says what it took
    assert not await AnniPlayer.filter(mc_uuid=baz.mc_uuid).exists()
    # The placement cascaded with the row (it was the only thing pointing at it).
    assert not await BoardPlacement.filter(event=event, player_id=baz.mc_uuid).exists()


async def test_purge_destroys_capabilities_and_rsvps_and_says_so(seeded):
    """The case the old shells-only rule blocked. It goes, everything hanging
    off it goes, and the result names what was destroyed — by then it is the
    only record that any of it existed."""
    wen = seeded["players"]["Wenweia"]
    assert await RoleCapability.filter(player=wen).exists()
    assert await Rsvp.filter(player=wen).exists()

    r = await players.purge(wen.mc_uuid)

    assert r.ok
    assert "destroyed what it held" in r.reason
    assert "capabilities" in r.reason            # most-decisive holding first
    assert not await AnniPlayer.filter(mc_uuid=wen.mc_uuid).exists()
    assert not await RoleCapability.filter(player_id=wen.mc_uuid).exists()
    assert not await Rsvp.filter(player_id=wen.mc_uuid).exists()


async def test_purging_a_host_and_organiser_nulls_those_seats(seeded):
    """Holidaze is the seed's organiser *and* party 1's host, with no
    capabilities/RSVP/password — so both holdings are provably the ones doing
    the work. Neither FK cascades: the party and the event survive, each minus
    a name, which is why allowing this is safe rather than merely permitted."""
    holidaze = seeded["players"]["Holidaze"]

    held = await players.holdings(holidaze)
    assert held == ["they are the host of Party 1",
                    "they are an anni's lead organiser"]

    r = await players.purge(holidaze.mc_uuid)

    assert r.ok and "host of Party 1" in r.reason
    assert not await AnniPlayer.filter(mc_uuid=holidaze.mc_uuid).exists()
    party1 = await Party.get(event=seeded["event"], ordinal=1)
    assert party1.host_id is None                # SET_NULL, not cascade
    assert (await AnniEvent.get(id=seeded["event"].id)).organizer_id is None


async def test_a_dashboard_login_is_reported_not_refused(seeded):
    """A password means they registered themselves — the strongest signal that
    this is somebody's real profile, so it leads the warning. It no longer
    stops the delete; the confirmation dialog does the stopping.

    ``Faulischlumpf`` is otherwise a shell (walk-in lane, no caps, no RSVP),
    so the password is provably the only thing being reported.
    """
    fz = seeded["players"]["Faulischlumpf"]
    assert await players.holdings(fz) == []

    fz.password_hash = "pbkdf2_sha256$whatever"
    await fz.save(update_fields=["password_hash"])

    assert await players.holdings(fz) == ["they have set a dashboard password"]
    r = await players.purge(fz.mc_uuid)
    assert r.ok and "dashboard password" in r.reason
    assert not await AnniPlayer.filter(mc_uuid=fz.mc_uuid).exists()


async def test_a_revoked_rsvp_still_counts_as_something_held(seeded):
    """Revoked RSVPs are kept as an audit trail (db/models.Rsvp) — they are
    still a human having said "I'm coming", so they belong in the warning."""
    from app.domain import rsvp as rsvp_domain

    event = seeded["event"]
    trix = seeded["players"]["Trixomaniac"]
    assert await rsvp_domain.revoke(trix, event) is not None

    assert any("RSVP" in h for h in await players.holdings(trix))


async def test_purge_of_an_unknown_uuid_is_a_friendly_reject(seeded):
    """The only refusal left."""
    r = await players.purge("00000000-0000-0000-0000-000000000000")
    assert not r.ok and "no longer exists" in r.reason


async def test_bulk_holdings_matches_the_per_player_form(seeded):
    """The roles dashboard builds every row's warning from the bulk pass, so
    if the two drift the dialog promises something the delete will not do."""
    bulk = await players.holdings_by_uuid()

    for player in await AnniPlayer.all():
        assert bulk.get(player.mc_uuid, []) == await players.holdings(player), (
            player.mc_username
        )

    # Sanity on the fixture: it contains both kinds.
    assert seeded["players"]["baz"].mc_uuid not in bulk
    assert "capabilities" in bulk[seeded["players"]["Wenweia"].mc_uuid][0]


async def test_deleting_the_last_capability_empties_the_warning(seeded):
    """The dashboard recomputes per render, so a staff capability delete can
    hand back a row whose confirmation has softened to "holds nothing"."""
    para = seeded["players"]["Paradrex"]        # one TERTIARY cap, one RSVP
    await Rsvp.filter(player=para).delete()     # ...and no RSVP history

    assert para.mc_uuid in await players.holdings_by_uuid()
    await RoleCapability.filter(player=para).delete()
    assert para.mc_uuid not in await players.holdings_by_uuid()
