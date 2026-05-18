"""The board mutation path — the single-instance-per-person invariant.

These assert the spec's hard rule at the *behaviour* level (the schema-level
constraint is already pinned by test_seed_invariants): every move is an UPSERT
of the one ``(event, player)`` row, re-adding an on-board player is an
idempotent no-op, and an ambiguous/unknown intent is a friendly reject — not a
duplicate, a crash, or a silent half-apply.
"""

from __future__ import annotations

from app.constants import BucketKind, PartyResult, Role
from app.db.models import BoardPlacement
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
    assert party.host_id == host.id


async def test_set_organizer_claim_and_release(seeded):
    event = seeded["event"]
    naz = seeded["players"]["Nazzae"]

    assert (await buckets.set_organizer(event, naz.mc_uuid)).ok
    await event.refresh_from_db()
    assert event.organizer_id == naz.id

    assert (await buckets.set_organizer(event, None)).ok
    await event.refresh_from_db()
    assert event.organizer_id is None
