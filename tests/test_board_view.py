"""board_view.snapshot — the one JSON-able board shape (SSR + WS).

Coverage focus is the surface area introduced for auto-population:

* ``is_placeholder`` propagates from ``AnniPlayer`` through ``board_rows``
  into the per-person dict so the template can render a stub card.
* ``rsvp_state``/``rsvp_chip`` carry the RSVP axis (the ticket left of the
  face) and must follow the user wherever they sit.
* the two derived soft-RSVP lanes: Unassigned takes EVERY soft RSVP
  (``is_soft``), a party takes only the offline ones (``is_offline_soft``),
  and the party one re-promotes on LIVE presence.
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
    await buckets.ensure_placed(event, stub)

    snap = await board_view.snapshot(event, AppState())
    u = snap["buckets"][BucketKind.UNASSIGNED.value]
    unassigned = u["on_time"] + u["soft"] + u["walkin"]
    me = next(m for m in unassigned if m["uuid"] == "uuid-stub")
    assert me["is_placeholder"] is True
    # Border channel MUST still be wired up (the placeholder card variant
    # only hides INNER stats — the status border lives on the root div).
    assert me["status_chip"] is not None
    assert "css_var" in me["status_chip"]


async def test_snapshot_marks_retraction_wherever_the_card_sits(seeded):
    """The torn ticket is keyed off ``Rsvp.revoked_at``, so it follows the
    user wherever they're placed. (The revoke path itself moves them to
    Sitting-out — this asserts the *rendering* is placement-independent, so
    a staff member who drags them back somewhere still sees the retraction.)
    """
    from datetime import datetime, timezone

    event = seeded["event"]
    wen = seeded["players"]["Wenweia"]  # seeded into a party with a HARD RSVP

    rsvp = await Rsvp.get(event=event, player=wen)
    rsvp.revoked_at = datetime.now(timezone.utc)
    await rsvp.save(update_fields=["revoked_at"])

    snap = await board_view.snapshot(event, AppState())
    members = [m for party in snap["parties"] for m in party["members"]]
    me = next(m for m in members if m["uuid"] == wen.mc_uuid)
    assert me["rsvp_state"] == RsvpState.REVOKED.value


async def test_snapshot_splits_unassigned_into_three_lanes(seeded):
    """Unassigned surfaces as disjoint lists keyed ``on_time``/``soft``/
    ``walkin``. There is no LATE lane — lateness became a per-card badge —
    so a seeded latecomer lands in a normal lane still carrying ``is_late``.
    """
    event = seeded["event"]
    snap = await board_view.snapshot(event, AppState())
    u = snap["buckets"][BucketKind.UNASSIGNED.value]
    assert "late" not in u
    assert len(u["soft"]) == 2                      # Paradrex + Trixomaniac
    assert all(m["rsvp_state"] == RsvpState.SOFT.value for m in u["soft"])
    # The walk-in lane is the stored flag, and never a soft RSVP.
    assert all(m["is_walkin"] for m in u["walkin"])
    assert all(not m["is_walkin"] for m in u["on_time"])
    # Seeded latecomers still exist; they are simply no longer segregated.
    everyone = u["on_time"] + u["soft"] + u["walkin"]
    assert any(m["is_late"] for m in everyone)
    # Disjoint: a single-instance person can't span two lanes.
    uuids = [m["uuid"] for m in everyone]
    assert len(uuids) == len(set(uuids))


async def test_unassigned_soft_lane_holds_online_soft_rsvps_too(seeded):
    """Unassigned's lane is EVERY soft RSVP — it is a queue an organiser is
    placing from, so they want all the maybes together. (A party's lane of
    the same shape is narrower; that one is asserted separately.)"""
    p = seeded["players"]
    event = seeded["event"]
    para, trix = p["Paradrex"].mc_uuid, p["Trixomaniac"].mc_uuid

    state = AppState(presence_by_uuid={
        para: PresenceStatus.OFFLINE.value,
        trix: PresenceStatus.ONLINE_ELSEWHERE.value,
    })
    u = (await board_view.snapshot(event, state))["buckets"]["unassigned"]
    assert {m["uuid"] for m in u["soft"]} == {para, trix}
    assert para not in {m["uuid"] for m in u["on_time"]}


async def test_party_offline_soft_lane_re_promotes_when_they_come_online(seeded):
    """A party's lane is derived from LIVE presence, which is the whole point:
    a soft RSVP who logs on is promoted back into the party proper on the next
    presence tick, with nobody dragging them. Nothing is stored — the same
    placement rows produce a different split as presence changes."""
    event = seeded["event"]
    naz = seeded["players"]["Nazzae"]   # party 1, seeded HARD
    rsvp = await Rsvp.get(event=event, player=naz)
    rsvp.notice = AttendanceNotice.RSVP_SOFT
    await rsvp.save(update_fields=["notice"])

    def party1(snap):
        return next(p for p in snap["parties"] if p["ordinal"] == 1)

    state = AppState(presence_by_uuid={naz.mc_uuid: PresenceStatus.OFFLINE.value})
    p1 = party1(await board_view.snapshot(event, state))
    assert naz.mc_uuid in {m["uuid"] for m in p1["offline_soft"]}

    # Having been here earlier makes no difference — they are still absent,
    # and the history is a badge, not a status.
    state.seen_online_uuids.add(naz.mc_uuid)
    p1 = party1(await board_view.snapshot(event, state))
    assert naz.mc_uuid in {m["uuid"] for m in p1["offline_soft"]}

    # UNKNOWN does NOT: unconfirmable is not absent, and parking someone on a
    # guess is the fabrication the spec forbids.
    state.presence_by_uuid[naz.mc_uuid] = PresenceStatus.UNKNOWN.value
    p1 = party1(await board_view.snapshot(event, state))
    assert naz.mc_uuid in {m["uuid"] for m in p1["members"]}

    # Log on => auto-promoted back into the party proper, no move recorded.
    state.presence_by_uuid[naz.mc_uuid] = PresenceStatus.ONLINE_ELSEWHERE.value
    p1 = party1(await board_view.snapshot(event, state))
    assert p1["offline_soft"] == []
    assert naz.mc_uuid in {m["uuid"] for m in p1["members"]}


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
            snap["buckets"]["unassigned"]["soft"],
            snap["buckets"]["unassigned"]["walkin"],
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
    # Chip carries the non-colour channels for every card.
    assert all(m["rsvp_chip"]["icon"] and m["rsvp_chip"]["label"]
               for m in everyone.values())

    # Revoked outranks the stored notice — Wenweia had a HARD RSVP.
    rsvp = await Rsvp.get(event=event, player=p["Wenweia"])
    rsvp.revoked_at = datetime.now(timezone.utc)
    await rsvp.save(update_fields=["revoked_at"])
    snap = await board_view.snapshot(event, AppState())
    wen = next(m for party in snap["parties"] for m in party["members"]
               if m["uuid"] == p["Wenweia"].mc_uuid)
    assert wen["rsvp_state"] == RsvpState.REVOKED.value


async def test_snapshot_reads_no_rsvp_row_as_walkin_not_revoked(seeded):
    """A user with no Rsvp row at all reads as a walk-in, never a retraction
    (avoid false positives on staff walk-ins / 1hr-early auto-adds)."""
    event = seeded["event"]
    walked = await AnniPlayer.create(mc_uuid="uuid-walk", mc_username="Walk")
    await buckets.ensure_placed(event, walked)

    snap = await board_view.snapshot(event, AppState())
    u = snap["buckets"][BucketKind.UNASSIGNED.value]
    unassigned = u["on_time"] + u["soft"] + u["walkin"]
    me = next(m for m in unassigned if m["uuid"] == "uuid-walk")
    assert me["rsvp_state"] == RsvpState.NONE.value
    assert me["is_placeholder"] is False


async def test_is_gone_marks_only_those_who_were_here_and_left(seeded):
    """The `?` stamp is derived, not stored: current status OFFLINE **and**
    the uuid in the poller's ``seen_online_uuids`` history. Neither half
    alone, and it clears the moment they log back on — which is the whole
    reason it is a separate axis from the border rather than a sixth status.
    """
    p = seeded["players"]
    event = seeded["event"]
    here_and_gone = p["Paradrex"].mc_uuid
    never_here = p["Trixomaniac"].mc_uuid

    state = AppState(
        presence_by_uuid={
            here_and_gone: PresenceStatus.OFFLINE.value,
            never_here: PresenceStatus.OFFLINE.value,
        },
        seen_online_uuids={here_and_gone},
    )

    def card(snap, uuid):
        u = snap["buckets"]["unassigned"]
        everyone = u["on_time"] + u["soft"] + u["walkin"]
        return next(m for m in everyone if m["uuid"] == uuid)

    snap = await board_view.snapshot(event, state)
    assert card(snap, here_and_gone)["is_gone"] is True
    assert card(snap, never_here)["is_gone"] is False
    # Both still read as plain OFFLINE — the badge does not touch the border.
    assert card(snap, here_and_gone)["status"] == PresenceStatus.OFFLINE.value

    # Back online: the history survives in the set, but the stamp clears.
    state.presence_by_uuid[here_and_gone] = PresenceStatus.ONLINE_ELSEWHERE.value
    snap = await board_view.snapshot(event, state)
    assert card(snap, here_and_gone)["is_gone"] is False
    assert here_and_gone in state.seen_online_uuids
