"""board_view.snapshot — the one JSON-able board shape (SSR + WS).

Coverage focus is the surface area introduced for auto-population:

* ``is_placeholder`` propagates from ``AnniPlayer`` through ``board_rows``
  into the per-person dict so the template can render a stub card.
* ``rsvp_state``/``rsvp_chip`` carry the RSVP axis (the badge left of the
  face) and ``rsvp_revoked`` reflects ``Rsvp.revoked_at`` regardless of
  bucket — both must follow the user wherever they sit.
* the derived "offline soft RSVPs" lane (``is_offline_soft``) splits both the
  Unassigned main lane and each party's member list on LIVE presence.
* ``event.monitoring`` + ``event.monitoring_label`` carry the live-pill
  state needed by ``static/js/board.js``.
"""

from __future__ import annotations

import time

from app.constants import (
    AttendanceNotice,
    BucketKind,
    PresenceStatus,
    RsvpState,
)
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


async def test_snapshot_splits_unassigned_into_four_lanes(seeded):
    """The seed populates every stored Unassigned sub-bucket and the snapshot
    surfaces them as disjoint lists keyed ``on_time``/``offline_soft``/
    ``walkin``/``late``. Boundaries: every member carries the matching
    ``is_late``/``is_walkin`` flags, and no member appears in more than one
    lane. With no presence data nobody is confirmed offline, so the derived
    ``offline_soft`` lane is empty here (its own test drives it)."""
    event = seeded["event"]
    snap = await board_view.snapshot(event, AppState())
    u = snap["buckets"][BucketKind.UNASSIGNED.value]
    assert len(u["on_time"]) == 4
    assert u["offline_soft"] == []
    assert len(u["walkin"]) == 2
    assert len(u["late"]) == 2
    # Per-card flags are consistent with the lane the snapshot put them in.
    assert all(not m["is_late"] and not m["is_walkin"] for m in u["on_time"])
    assert all(not m["is_late"] and m["is_walkin"] for m in u["walkin"])
    assert all(m["is_late"] for m in u["late"])
    # Disjoint: a single-instance person can't span two lanes.
    uuids = (
        [m["uuid"] for m in u["on_time"]]
        + [m["uuid"] for m in u["offline_soft"]]
        + [m["uuid"] for m in u["walkin"]]
        + [m["uuid"] for m in u["late"]]
    )
    assert len(uuids) == len(set(uuids))


async def test_offline_soft_lane_is_derived_from_live_presence(seeded):
    """Soft RSVP **and** offline is what carves a card out of the Unassigned
    main lane — neither half alone, and the card returns the moment presence
    says they are back. Nothing is stored: the same placement rows produce a
    different split as the presence map changes."""
    p = seeded["players"]
    event = seeded["event"]
    para = p["Paradrex"].mc_uuid       # soft RSVP, Unassigned main
    trix = p["Trixomaniac"].mc_uuid    # soft RSVP, Unassigned main
    foo = p["foo"].mc_uuid             # HARD RSVP, Unassigned main

    # Everyone offline: only the two soft RSVPs move.
    state = AppState(presence_by_uuid={
        u: PresenceStatus.OFFLINE.value for u in (para, trix, foo)
    })
    u = (await board_view.snapshot(event, state))["buckets"]["unassigned"]
    assert {m["uuid"] for m in u["offline_soft"]} == {para, trix}
    assert foo in {m["uuid"] for m in u["on_time"]}   # hard RSVP stays put

    # GONE counts as offline too (they were here and left — still absent).
    state.presence_by_uuid[para] = PresenceStatus.OFFLINE_GONE.value
    u = (await board_view.snapshot(event, state))["buckets"]["unassigned"]
    assert para in {m["uuid"] for m in u["offline_soft"]}

    # UNKNOWN does NOT: unconfirmable is not absent, and parking someone on a
    # guess is the fabrication the spec forbids.
    state.presence_by_uuid[para] = PresenceStatus.UNKNOWN.value
    u = (await board_view.snapshot(event, state))["buckets"]["unassigned"]
    assert para in {m["uuid"] for m in u["on_time"]}

    # Log on and the card returns to the main lane with no move recorded.
    state.presence_by_uuid[trix] = PresenceStatus.ONLINE_ELSEWHERE.value
    u = (await board_view.snapshot(event, state))["buckets"]["unassigned"]
    assert {m["uuid"] for m in u["offline_soft"]} == set()
    assert {para, trix, foo} <= {m["uuid"] for m in u["on_time"]}


async def test_offline_soft_lane_leaves_walkin_and_late_intact(seeded):
    """The derived lane is carved out of the MAIN lane only. Walk-ins never
    RSVP'd so they cannot qualify; LATE is a provenance marker worth keeping
    whole, so a late soft-RSVP stays in LATE."""
    event = seeded["event"]
    salted = seeded["players"]["Salted"]       # seeded into the LATE lane
    await Rsvp.create(event=event, player=salted,
                      notice=AttendanceNotice.RSVP_SOFT)

    state = AppState(presence_by_uuid={
        salted.mc_uuid: PresenceStatus.OFFLINE.value,
    })
    u = (await board_view.snapshot(event, state))["buckets"]["unassigned"]
    assert salted.mc_uuid in {m["uuid"] for m in u["late"]}
    assert salted.mc_uuid not in {m["uuid"] for m in u["offline_soft"]}


async def test_party_offline_soft_lane_splits_members_but_not_the_count(seeded):
    """A party's offline soft RSVPs get their own sub-list, while ``count``
    still spans both — they are occupying a slot either way, and the N/10
    header must not lie."""
    event = seeded["event"]
    naz = seeded["players"]["Nazzae"]   # party 1, seeded HARD
    rsvp = await Rsvp.get(event=event, player=naz)
    rsvp.notice = AttendanceNotice.RSVP_SOFT
    await rsvp.save(update_fields=["notice"])

    state = AppState(presence_by_uuid={
        naz.mc_uuid: PresenceStatus.OFFLINE.value,
    })
    snap = await board_view.snapshot(event, state)
    party1 = next(p for p in snap["parties"] if p["ordinal"] == 1)
    assert [m["uuid"] for m in party1["offline_soft"]] == [naz.mc_uuid]
    assert naz.mc_uuid not in {m["uuid"] for m in party1["members"]}
    assert party1["count"] == len(party1["members"]) + 1


async def test_person_carries_the_rsvp_axis(seeded):
    """The RSVP badge is its own channel: state + a chip with a glyph/label,
    on every card, independent of the status border."""
    from datetime import datetime, timezone

    event = seeded["event"]
    p = seeded["players"]
    snap = await board_view.snapshot(event, AppState())
    everyone = {
        m["uuid"]: m
        for lane in (
            [x for party in snap["parties"]
             for x in party["members"] + party["offline_soft"]],
            snap["buckets"]["unassigned"]["on_time"],
            snap["buckets"]["unassigned"]["offline_soft"],
            snap["buckets"]["unassigned"]["walkin"],
            snap["buckets"]["unassigned"]["late"],
            snap["buckets"]["volunteers"]["members"],
            snap["buckets"]["wontassign"]["members"],
        )
        for m in lane
    }
    assert everyone[p["Wenweia"].mc_uuid]["rsvp_state"] == RsvpState.HARD.value
    assert everyone[p["Paradrex"].mc_uuid]["rsvp_state"] == RsvpState.SOFT.value
    # No Rsvp row at all => a walk-in, never a false "retracted".
    faul = everyone[p["Faulischlumpf"].mc_uuid]
    assert faul["rsvp_state"] == RsvpState.NONE.value
    assert faul["rsvp_revoked"] is False
    # Chip carries the non-colour channels for every card.
    assert all(m["rsvp_chip"]["glyph"] and m["rsvp_chip"]["label"]
               for m in everyone.values())

    # Revoked outranks the stored notice — Wenweia had a HARD RSVP.
    rsvp = await Rsvp.get(event=event, player=p["Wenweia"])
    rsvp.revoked_at = datetime.now(timezone.utc)
    await rsvp.save(update_fields=["revoked_at"])
    snap = await board_view.snapshot(event, AppState())
    wen = next(m for party in snap["parties"] for m in party["members"]
               if m["uuid"] == p["Wenweia"].mc_uuid)
    assert wen["rsvp_state"] == RsvpState.REVOKED.value
    assert wen["rsvp_revoked"] is True


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
