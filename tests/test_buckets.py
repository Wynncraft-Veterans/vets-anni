"""The board mutation path — the single-instance-per-person invariant.

These assert the spec's hard rule at the *behaviour* level (the schema-level
constraint is already pinned by test_seed_invariants): every move is an UPSERT
of the one ``(event, player)`` row, re-adding an on-board player is an
idempotent no-op, and an ambiguous/unknown intent is a friendly reject — not a
duplicate, a crash, or a silent half-apply.
"""

from __future__ import annotations

from app.constants import BucketKind, PartyResult, Role
from app.db.models import BoardPlacement, Party
from app.domain import buckets
from app.services.state import AppState


async def test_move_is_an_upsert_never_a_duplicate(seeded):
    event = seeded["event"]
    wen = seeded["players"]["Wenweia"]  # seeded into party 1 as PRIMARY

    before = await BoardPlacement.filter(event=event).count()
    r = await buckets.move(event, wen.mc_uuid,
                           bucket=BucketKind.WONTASSIGN, sort_index=0)
    assert r.ok
    after = await BoardPlacement.filter(event=event).count()
    assert after == before  # moved, not added

    rows = await BoardPlacement.filter(event=event, player=wen)
    assert len(rows) == 1
    assert rows[0].bucket is BucketKind.WONTASSIGN
    assert rows[0].party_id is None  # leaving the party cleared the FK


async def test_move_rejects_ambiguous_and_unknown_targets(seeded):
    event = seeded["event"]
    wen = seeded["players"]["Wenweia"]

    # Neither container -> rejected (the BoardPlacement shape invariant).
    r = await buckets.move(event, wen.mc_uuid)
    assert not r.ok and "exactly one place" in r.reason

    r = await buckets.move(event, "no-such-uuid", bucket=BucketKind.UNASSIGNED)
    assert not r.ok and "no longer known" in r.reason

    r = await buckets.move(event, wen.mc_uuid, party_id="deadbeef")
    assert not r.ok and "party" in r.reason.lower()


async def test_assign_role_sets_and_clears(seeded):
    event = seeded["event"]
    fz = seeded["players"]["Faulischlumpf"]  # in Unassigned, no role

    assert (await buckets.assign_role(event, fz.mc_uuid, Role.TANK)).ok
    p = await BoardPlacement.get(event=event, player=fz)
    assert p.assigned_role is Role.TANK

    assert (await buckets.assign_role(event, fz.mc_uuid, None)).ok
    p = await BoardPlacement.get(event=event, player=fz)
    assert p.assigned_role is None  # -> grey unassigned


async def test_add_walkin_resolves_creates_and_is_idempotent(seeded):
    event = seeded["event"]
    state = AppState(roster_by_uuid={"uuid-walkin": "Walkin"})

    async def _no_mojang(_ign):
        return None  # roster must resolve it without any network

    n0 = await BoardPlacement.filter(event=event).count()
    r = await buckets.add_walkin(event, "Walkin", state, mojang=_no_mojang)
    assert r.ok and r.player_uuid == "uuid-walkin"
    placed = await BoardPlacement.get(event=event,
                                      player__mc_uuid="uuid-walkin")
    assert placed.bucket is BucketKind.UNASSIGNED
    assert await BoardPlacement.filter(event=event).count() == n0 + 1

    # Re-adding an on-board player is a NO-OP (single-instance held): no
    # duplicate AND it must not yank them back to Unassigned.
    await buckets.move(event, "uuid-walkin", bucket=BucketKind.VOLUNTEERS)
    r = await buckets.add_walkin(event, "Walkin", state, mojang=_no_mojang)
    assert r.ok
    assert await BoardPlacement.filter(event=event).count() == n0 + 1
    again = await BoardPlacement.get(event=event,
                                     player__mc_uuid="uuid-walkin")
    assert again.bucket is BucketKind.VOLUNTEERS  # unchanged, not reset


async def test_add_walkin_unknown_ign_is_a_friendly_reject(seeded):
    async def _no_mojang(_ign):
        return None

    r = await buckets.add_walkin(seeded["event"], "ghost", AppState(),
                                 mojang=_no_mojang)
    assert not r.ok and "Couldn't find" in r.reason


# --- ensure_placed + demote_on_revoke (auto-promoter + RSVP support) --------
async def test_ensure_placed_creates_in_main_lane(seeded):
    """A brand-new auto-place lands in Unassigned with is_late as supplied."""
    from app.db.models import AnniPlayer
    event = seeded["event"]
    fresh = await AnniPlayer.create(mc_uuid="uuid-fresh", mc_username="Fresh")

    inserted = await buckets.ensure_placed(event, fresh, is_late=False)
    assert inserted is True
    placed = await BoardPlacement.get(event=event, player=fresh)
    assert placed.bucket is BucketKind.UNASSIGNED
    assert placed.is_late is False


async def test_ensure_placed_creates_in_late_lane(seeded):
    from app.db.models import AnniPlayer
    event = seeded["event"]
    latecomer = await AnniPlayer.create(mc_uuid="uuid-late", mc_username="Late")

    inserted = await buckets.ensure_placed(event, latecomer, is_late=True)
    assert inserted is True
    placed = await BoardPlacement.get(event=event, player=latecomer)
    assert placed.bucket is BucketKind.UNASSIGNED
    assert placed.is_late is True


async def test_ensure_placed_is_idempotent_no_reshuffle(seeded):
    """A second call must never yank an existing placement back to Unassigned
    or flip is_late retroactively. Single-instance + staff-intent wins."""
    from app.db.models import AnniPlayer
    event = seeded["event"]
    p = await AnniPlayer.create(mc_uuid="uuid-shuffled", mc_username="Shuf")

    assert await buckets.ensure_placed(event, p, is_late=False) is True
    # Staff (or another path) moves them to a party slot.
    party = await buckets.create_party(event)
    assert (await buckets.move(event, p.mc_uuid, party_id=str(party.id))).ok

    # A subsequent ensure_placed during the LATE window MUST NOT yank them
    # out of their party — the auto-promoter is idempotent on placed users.
    assert await buckets.ensure_placed(event, p, is_late=True) is False
    placed = await BoardPlacement.get(event=event, player=p)
    assert placed.party_id == party.id
    assert placed.bucket is None
    # And of course no duplicate row.
    assert await BoardPlacement.filter(event=event, player=p).count() == 1


async def test_ensure_placed_late_lane_sort_index_tails(seeded):
    """is_late lane order: each new auto-place lands at the tail of its lane."""
    from app.db.models import AnniPlayer
    event = seeded["event"]
    a = await AnniPlayer.create(mc_uuid="uuid-a", mc_username="A")
    b = await AnniPlayer.create(mc_uuid="uuid-b", mc_username="B")

    await buckets.ensure_placed(event, a, is_late=True)
    await buckets.ensure_placed(event, b, is_late=True)
    pa = await BoardPlacement.get(event=event, player=a)
    pb = await BoardPlacement.get(event=event, player=b)
    assert pa.sort_index != pb.sort_index  # distinct tail slots
    assert pa.is_late and pb.is_late


async def test_demote_on_revoke_moves_unassigned_to_wontassign(seeded):
    from app.db.models import AnniPlayer
    event = seeded["event"]
    p = await AnniPlayer.create(mc_uuid="uuid-rev", mc_username="Rev")
    await buckets.ensure_placed(event, p, is_late=False)

    demoted = await buckets.demote_on_revoke(event, p)
    assert demoted is True
    placed = await BoardPlacement.get(event=event, player=p)
    assert placed.bucket is BucketKind.WONTASSIGN
    assert placed.party_id is None


async def test_demote_on_revoke_noop_when_in_party(seeded):
    """Staff intent wins: if they've already placed the user in a party,
    a revoke MUST NOT yank them out (the Retracted pill on the card is the
    only visible signal)."""
    event = seeded["event"]
    wen = seeded["players"]["Wenweia"]  # seeded into party 1

    demoted = await buckets.demote_on_revoke(event, wen)
    assert demoted is False
    placed = await BoardPlacement.get(event=event, player=wen)
    assert placed.party_id is not None  # unchanged


async def test_demote_on_revoke_noop_when_no_placement(seeded):
    """No placement at all -> no-op (a revoke of an RSVP that never landed)."""
    from app.db.models import AnniPlayer
    event = seeded["event"]
    ghost = await AnniPlayer.create(mc_uuid="uuid-ghost", mc_username="Ghost")

    demoted = await buckets.demote_on_revoke(event, ghost)
    assert demoted is False
    assert await BoardPlacement.filter(event=event, player=ghost).count() == 0


async def test_party_create_rename_and_set(seeded):
    event = seeded["event"]
    party = await buckets.create_party(event)
    assert party.ordinal == 3  # seed has parties 1 and 2

    # Ordinal stays unique per event.
    clash = await buckets.rename_party(event, str(party.id), 1)
    assert not clash.ok and "already exists" in clash.reason
    assert (await buckets.rename_party(event, str(party.id), 5)).ok

    host = seeded["players"]["Holidaze"]
    r = await buckets.set_party(event, str(party.id), host_uuid=host.mc_uuid,
                                world="NA12", stage=9, result="win")
    assert r.ok
    await party.refresh_from_db()
    assert party.world == "NA12"
    assert party.stage == 5  # clamped to MAX_PARTY_STAGE
    assert party.result is PartyResult.WIN
    assert party.host_id == host.mc_uuid


async def test_delete_party_only_when_empty(seeded):
    event = seeded["event"]

    # A freshly-created party has no members -> deletes cleanly.
    fresh = await buckets.create_party(event)
    n_before = await Party.filter(event=event).count()
    r = await buckets.delete_party(event, str(fresh.id))
    assert r.ok
    assert await Party.filter(event=event).count() == n_before - 1

    # Seeded party 1 has members (Wenweia et al.) -> friendly REJECTED, and
    # the row is still there so members aren't stranded.
    wen = seeded["players"]["Wenweia"]
    party1 = await BoardPlacement.get(event=event, player=wen).values("party_id")
    r = await buckets.delete_party(event, str(party1["party_id"]))
    assert not r.ok and "Move its members" in r.reason

    # Unknown id -> friendly REJECTED, not a crash.
    r = await buckets.delete_party(event, "00000000-0000-0000-0000-000000000000")
    assert not r.ok and "no longer exists" in r.reason


async def test_set_organizer_claim_and_release(seeded):
    event = seeded["event"]
    naz = seeded["players"]["Nazzae"]

    assert (await buckets.set_organizer(event, naz.mc_uuid)).ok
    await event.refresh_from_db()
    assert event.organizer_id == naz.mc_uuid

    assert (await buckets.set_organizer(event, None)).ok
    await event.refresh_from_db()
    assert event.organizer_id is None
