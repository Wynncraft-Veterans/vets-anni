"""Reorder + role-priority insert semantics for BoardPlacement.sort_index.

The rules under test (see .claude/data_model.md + app/domain/buckets._upsert):

* Every move renumbers the destination container so ``sort_index`` values
  are contiguous ``0..N-1`` in the resulting order — collisions are
  physically impossible after any single mutation. Without this, drag-
  reorder within a bucket doesn't stick because tortoise's
  ``.order_by("sort_index")`` has no stable tiebreaker for equal values.
* Auto-place paths (walk-in add, RSVP ensure-placed, revoke demote)
  choose their slot by role priority so a bucket's *default* order is
  Primary > Secondary > Tertiary > Healer > Tank > Fill > unassigned.
  Explicit drags (``move()`` with a caller-supplied ``sort_index``) still
  override this — the drag position is authoritative.
"""

from __future__ import annotations

from app.constants import BucketKind, Role
from app.db.models import AnniPlayer, BoardPlacement
from app.domain import buckets
from app.services.state import AppState


# --- within-lane reorder ---------------------------------------------------
async def test_within_lane_reorder_lands_at_target_and_siblings_renumber(seeded):
    """Drag the last main-lane Unassigned card up to index 1 → it becomes
    sort_index=1 and no two cards share a sort_index. Regression for the
    original bug: prior to the renumber, F.sort_index=1 collided with the
    existing card at 1 and the render tiebreaker was undefined."""
    event = seeded["event"]
    # Seed puts Metrafish/Paradrex/Trixomaniac/foo at i=0..3 in the main lane.
    lane = BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED,
        is_walkin=False,
    )
    before = await lane.order_by("sort_index")
    assert len(before) >= 4, "seed populates at least four main-lane cards"
    last = before[-1]

    r = await buckets.move(
        event, last.player_id,
        bucket=BucketKind.UNASSIGNED, sort_index=1,
        is_walkin=False,
    )
    assert r.ok

    after = await lane.order_by("sort_index")
    indexes = [p.sort_index for p in after]
    assert indexes == list(range(len(after))), \
        f"sort_indexes must be contiguous 0..N-1, got {indexes}"
    # The moved player is now at position 1.
    moved = next(p for p in after if p.player_id == last.player_id)
    assert moved.sort_index == 1


async def test_within_lane_reorder_target_beyond_tail_clamps(seeded):
    """A drop past the tail (client sends an out-of-range sort_index) clamps
    to the end of the lane — no negatives, no gaps, no crash."""
    event = seeded["event"]
    lane = BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED,
        is_walkin=False,
    )
    first = (await lane.order_by("sort_index"))[0]

    r = await buckets.move(
        event, first.player_id,
        bucket=BucketKind.UNASSIGNED, sort_index=9999,
        is_walkin=False,
    )
    assert r.ok

    after = await lane.order_by("sort_index")
    assert [p.sort_index for p in after] == list(range(len(after)))
    assert after[-1].player_id == first.player_id


async def test_cross_lane_move_renumbers_destination_only(seeded):
    """Moving a card from the walk-in lane into the on-time lane at index 0
    renumbers the destination lane (0..N-1). The source is left as-is —
    gaps are display-invisible and heal on the next insert."""
    event = seeded["event"]
    walkin = BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED,
        is_walkin=True,
    )
    walker = (await walkin.order_by("sort_index"))[0]

    r = await buckets.move(
        event, walker.player_id,
        bucket=BucketKind.UNASSIGNED, sort_index=0,
        is_walkin=False,
    )
    assert r.ok

    on_time = await BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED,
        is_walkin=False,
    ).order_by("sort_index")
    assert [p.sort_index for p in on_time] == list(range(len(on_time)))
    assert on_time[0].player_id == walker.player_id


# --- role-priority slot on auto-place --------------------------------------
async def test_auto_place_lands_after_all_role_assigned_cards(seeded):
    """A fresh auto-place has no assigned role (priority tail). If existing
    cards in the lane have assigned roles Primary/Secondary/Tank, the new
    card must land AFTER all of them, not between."""
    event = seeded["event"]
    # Give three main-lane cards distinct roles so the lane has role signal.
    lane_ids = [
        p.player_id for p in (await BoardPlacement.filter(
            event=event, bucket=BucketKind.UNASSIGNED,
            is_walkin=False,
        ).order_by("sort_index"))[:3]
    ]
    for uuid, role in zip(lane_ids, [Role.PRIMARY, Role.SECONDARY, Role.TANK]):
        assert (await buckets.assign_role(event, uuid, role)).ok

    fresh = await AnniPlayer.create(mc_uuid="uuid-fresh-role",
                                    mc_username="FreshRole")
    inserted = await buckets.ensure_placed(event, fresh)
    assert inserted is True

    placed = await BoardPlacement.get(event=event, player=fresh)
    # Every card in the lane at insert time had a role (priority < 6); the
    # newcomer is None (priority 6) so slots at the tail.
    lane = await BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED,
        is_walkin=False,
    ).order_by("sort_index")
    assert placed.sort_index == lane[-1].sort_index
    assert lane[-1].player_id == fresh.mc_uuid


async def test_auto_place_walkin_uses_role_priority_slot(seeded):
    """The staff walk-in path (add_walkin) also slots by role priority.
    Seed's walkin lane has Faulischlumpf(0)+baz(1) already. Assign both
    a role (Primary+Fill), then add a new IGN — it must land at the tail
    (no assigned role → priority 6, below Fill's 5)."""
    from app.db.models import AnniPlayer
    event = seeded["event"]

    walkin = await BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED,
        is_walkin=True,
    ).order_by("sort_index")
    assert len(walkin) >= 2
    assert (await buckets.assign_role(
        event, walkin[0].player_id, Role.PRIMARY)).ok
    assert (await buckets.assign_role(
        event, walkin[1].player_id, Role.FILL)).ok

    # Stub the roster so add_walkin's identity.resolve_identity succeeds.
    state = AppState(roster_by_uuid={"uuid-w1": "NewWalker"})
    async def _no_mojang(_ign):
        return None
    r = await buckets.add_walkin(event, "NewWalker", state, mojang=_no_mojang)
    assert r.ok

    final = await BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED,
        is_walkin=True,
    ).select_related("player").order_by("sort_index")
    # The new walker sits at the tail (assigned_role=None > FILL).
    assert final[-1].player.mc_username == "NewWalker"
    assert [p.sort_index for p in final] == list(range(len(final)))


# --- assign_role does NOT re-sort (deliberate ergonomics call) -------------
async def test_assign_role_does_not_move_the_card(seeded):
    """Changing a card's role via the dropdown must not shuffle it. Rationale
    (plan §5): an organiser who dragged a card to a specific slot doesn't
    want a role-dropdown change to silently undo their arrangement."""
    event = seeded["event"]
    fz = seeded["players"]["Faulischlumpf"]

    before = await BoardPlacement.get(event=event, player=fz)
    original_index = before.sort_index
    original_bucket = before.bucket
    original_walkin = before.is_walkin

    assert (await buckets.assign_role(event, fz.mc_uuid, Role.PRIMARY)).ok

    after = await BoardPlacement.get(event=event, player=fz)
    assert after.sort_index == original_index
    assert after.bucket is original_bucket
    assert after.is_walkin == original_walkin
