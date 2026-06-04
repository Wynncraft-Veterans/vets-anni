"""board_view.snapshot — the one JSON-able board shape (SSR + WS).

Coverage focus is the surface area introduced for auto-population:

* ``is_placeholder`` propagates from ``AnniPlayer`` through ``board_rows``
  into the per-person dict so the template can render a stub card.
* ``rsvp_revoked`` reflects ``Rsvp.revoked_at`` regardless of bucket (the
  Retracted pill must follow the user wherever they sit).
* ``event.monitoring`` + ``event.monitoring_label`` carry the live-pill
  state needed by ``static/js/board.js``.
"""

from __future__ import annotations

import time

from app.constants import AttendanceNotice, BucketKind
from app.db.models import AnniPlayer, BoardPlacement, Rsvp
from app.domain import buckets
from app.services.state import AppState
from app.web import board_view


async def test_snapshot_emits_monitoring_idle_when_far_from_anni(seeded):
    event = seeded["event"]
    event.stamp_epoch = int(time.time()) + 4 * 3600  # T-4h
    await event.save(update_fields=["stamp_epoch"])

    snap = await board_view.snapshot(event, AppState())
    assert snap["event"]["monitoring"] == "idle"
    assert "not yet monitoring" in snap["event"]["monitoring_label"]


async def test_snapshot_emits_monitoring_early_then_late(seeded):
    event = seeded["event"]

    event.stamp_epoch = int(time.time()) + 65 * 60  # T-65min => early
    await event.save(update_fields=["stamp_epoch"])
    snap = await board_view.snapshot(event, AppState())
    assert snap["event"]["monitoring"] == "early"
    assert "1hr+ early" in snap["event"]["monitoring_label"]

    event.stamp_epoch = int(time.time()) + 30 * 60  # T-30min => late
    await event.save(update_fields=["stamp_epoch"])
    snap = await board_view.snapshot(event, AppState())
    assert snap["event"]["monitoring"] == "late"
    assert "late players" in snap["event"]["monitoring_label"]


async def test_snapshot_marks_placeholder_player(seeded):
    """A player materialised by the auto-promoter as a placeholder should
    surface ``is_placeholder=True`` on their person dict so the template
    can render a stub card. Status-border still needs to track — assert
    ``status_chip`` is populated regardless."""
    event = seeded["event"]
    stub = await AnniPlayer.create(
        mc_uuid="uuid-stub", mc_username="Stub", is_placeholder=True,
    )
    await buckets.ensure_placed(event, stub, is_late=False)

    snap = await board_view.snapshot(event, AppState())
    u = snap["buckets"][BucketKind.UNASSIGNED.value]
    unassigned = u["on_time"] + u["walkin"] + u["late"]
    me = next(m for m in unassigned if m["uuid"] == "uuid-stub")
    assert me["is_placeholder"] is True
    # Border channel MUST still be wired up (the placeholder card variant
    # only hides INNER stats — the status border lives on the root div).
    assert me["status_chip"] is not None
    assert "css_var" in me["status_chip"]


async def test_snapshot_marks_rsvp_revoked_across_buckets(seeded):
    """The Retracted pill flag is keyed off ``Rsvp.revoked_at`` so it follows
    the user wherever they're placed — Unassigned, party slot, wontassign."""
    from datetime import datetime, timezone

    event = seeded["event"]
    wen = seeded["players"]["Wenweia"]  # seeded into a party with a HARD RSVP

    # Mark Wenweia's RSVP revoked.
    rsvp = await Rsvp.get(event=event, player=wen)
    rsvp.revoked_at = datetime.now(timezone.utc)
    await rsvp.save(update_fields=["revoked_at"])

    snap = await board_view.snapshot(event, AppState())
    # Find Wenweia inside whichever party she landed in.
    members = [m for party in snap["parties"] for m in party["members"]]
    me = next(m for m in members if m["uuid"] == wen.mc_uuid)
    assert me["rsvp_revoked"] is True


async def test_snapshot_splits_unassigned_into_three_lanes(seeded):
    """The seed populates every Unassigned sub-bucket and the snapshot
    surfaces them as three disjoint lists keyed ``on_time``/``walkin``/
    ``late``. Boundaries: every member carries the matching ``is_late``/
    ``is_walkin`` flags, and no member appears in more than one lane."""
    event = seeded["event"]
    snap = await board_view.snapshot(event, AppState())
    u = snap["buckets"][BucketKind.UNASSIGNED.value]
    assert len(u["on_time"]) == 4
    assert len(u["walkin"]) == 2
    assert len(u["late"]) == 2
    # Per-card flags are consistent with the lane the snapshot put them in.
    assert all(not m["is_late"] and not m["is_walkin"] for m in u["on_time"])
    assert all(not m["is_late"] and m["is_walkin"] for m in u["walkin"])
    assert all(m["is_late"] for m in u["late"])
    # Disjoint: a single-instance person can't span two lanes.
    uuids = (
        [m["uuid"] for m in u["on_time"]]
        + [m["uuid"] for m in u["walkin"]]
        + [m["uuid"] for m in u["late"]]
    )
    assert len(uuids) == len(set(uuids))


async def test_snapshot_skips_revoked_pill_when_no_rsvp_row(seeded):
    """A user with no Rsvp row at all should not carry rsvp_revoked=True
    (avoid false positives on staff walk-ins / 1hr-early auto-adds)."""
    event = seeded["event"]
    walked = await AnniPlayer.create(mc_uuid="uuid-walk", mc_username="Walk")
    await buckets.ensure_placed(event, walked, is_late=False)

    snap = await board_view.snapshot(event, AppState())
    u = snap["buckets"][BucketKind.UNASSIGNED.value]
    unassigned = u["on_time"] + u["walkin"] + u["late"]
    me = next(m for m in unassigned if m["uuid"] == "uuid-walk")
    assert me["rsvp_revoked"] is False
    assert me["is_placeholder"] is False
