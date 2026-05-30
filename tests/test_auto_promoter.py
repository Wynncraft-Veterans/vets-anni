"""auto_promoter — the 1hr-early board landing path + boot-heal sweep.

The spec contract: from T-70 through grace, every online guild member (and
every outstanding RSVP that pre-dates this tick) gets a BoardPlacement.
Online UUIDs with no AnniPlayer row become a placeholder card; existing
players are never re-flipped to placeholder.

Tests stub the broadcast (no real WS) and exercise the tick by hand so we
can control ``now`` (and therefore the hot/late switch) without sleeping.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from app.constants import BucketKind
from app.db.models import AnniPlayer, BoardPlacement
from app.services import auto_promoter, hot_window
from app.services.state import AppState, OnlinePlayer
from app.settings import Settings


def _settings_for(stamp_offset: int) -> tuple[Settings, int]:
    """Settings + a stamp_epoch ``stamp_offset`` seconds from now (positive =
    in the future). Returns the offset baked into the active event already
    created by ``seeded`` — we use ``stamp_offset`` to *change* the event's
    stamp to control which side of the T-60 / T-70 boundaries we sit on."""
    return Settings(), stamp_offset


async def _set_event_stamp(seeded, stamp_offset_seconds: int) -> None:
    """Pin the seeded event's stamp_epoch to ``now + offset``."""
    event = seeded["event"]
    event.stamp_epoch = int(time.time()) + stamp_offset_seconds
    await event.save(update_fields=["stamp_epoch"])


async def _patch_broadcast(monkeypatch) -> AsyncMock:
    """Replace ``get_board_hub().broadcast_snapshot`` with a counting stub."""
    from app.web.ws import board_hub as board_hub_mod

    hub = board_hub_mod.BoardHub()
    hub.broadcast_snapshot = AsyncMock(return_value=1)
    monkeypatch.setattr(board_hub_mod, "get_board_hub", lambda: hub)
    return hub.broadcast_snapshot


async def _reset_sweep_guard(monkeypatch):
    """Reset the module-level boot-heal guard so each test runs the sweep."""
    monkeypatch.setattr(auto_promoter, "_swept", False)


async def test_idle_outside_hot_window_no_inserts(seeded, monkeypatch):
    """T-3h: bail on the hot gate, no placements created, no broadcast.
    (The hot window opens at T-2h, so the test offset must be strictly
    further out than that to be 'idle'.)"""
    broadcast = await _patch_broadcast(monkeypatch)
    await _reset_sweep_guard(monkeypatch)
    await _set_event_stamp(seeded, 3 * 3600)  # T-3h before the anni
    state = AppState(online_by_uuid={
        "uuid-online": OnlinePlayer(uuid="uuid-online", username="Online")
    })

    n0 = await BoardPlacement.filter(event=seeded["event"]).count()
    await auto_promoter._tick(state, Settings())
    assert await BoardPlacement.filter(event=seeded["event"]).count() == n0
    broadcast.assert_not_called()
    assert hot_window.is_currently_hot() is False


async def test_inside_hot_window_lands_online_users(seeded, monkeypatch):
    """T-65min (in hot, before LATE switch): online player gets is_late=False."""
    broadcast = await _patch_broadcast(monkeypatch)
    await _reset_sweep_guard(monkeypatch)
    await _set_event_stamp(seeded, 65 * 60)
    # An online player who isn't on the board yet AND has no AnniPlayer row.
    state = AppState(online_by_uuid={
        "uuid-new": OnlinePlayer(uuid="uuid-new", username="NewKid")
    })

    await auto_promoter._tick(state, Settings())

    placed = await BoardPlacement.get(
        event=seeded["event"], player__mc_uuid="uuid-new",
    )
    assert placed.bucket is BucketKind.UNASSIGNED
    assert placed.is_late is False
    # Placeholder AnniPlayer was materialised.
    player = await AnniPlayer.get(mc_uuid="uuid-new")
    assert player.is_placeholder is True
    # One broadcast for the whole tick, even with multiple inserts possible.
    broadcast.assert_awaited_once()
    assert hot_window.is_currently_hot() is True


async def test_late_window_uses_late_lane(seeded, monkeypatch):
    """T-30min (past T-60): new auto-placements land in the LATE sub-bucket."""
    await _patch_broadcast(monkeypatch)
    await _reset_sweep_guard(monkeypatch)
    await _set_event_stamp(seeded, 30 * 60)
    state = AppState(online_by_uuid={
        "uuid-tardy": OnlinePlayer(uuid="uuid-tardy", username="Tardy")
    })

    await auto_promoter._tick(state, Settings())

    placed = await BoardPlacement.get(
        event=seeded["event"], player__mc_uuid="uuid-tardy",
    )
    assert placed.bucket is BucketKind.UNASSIGNED
    assert placed.is_late is True


async def test_existing_player_not_re_flipped_to_placeholder(seeded, monkeypatch):
    """An existing AnniPlayer that the auto-promoter sees online again MUST
    keep its real-user state — the placeholder flag flows True -> False only."""
    await _patch_broadcast(monkeypatch)
    await _reset_sweep_guard(monkeypatch)
    await _set_event_stamp(seeded, 65 * 60)
    # Pick an existing seeded player who isn't already placed (e.g. someone
    # without a board placement). Create a fresh player for determinism.
    p = await AnniPlayer.create(
        mc_uuid="uuid-existing", mc_username="Existing", is_placeholder=False,
    )
    state = AppState(online_by_uuid={
        p.mc_uuid: OnlinePlayer(uuid=p.mc_uuid, username="Existing")
    })

    await auto_promoter._tick(state, Settings())

    await p.refresh_from_db()
    assert p.is_placeholder is False  # NOT re-flipped


async def test_boot_heal_sweeps_outstanding_rsvps(seeded, monkeypatch):
    """First hot tick after process start: every non-revoked Rsvp lands on
    the board even if the player isn't currently online."""
    await _patch_broadcast(monkeypatch)
    await _reset_sweep_guard(monkeypatch)
    await _set_event_stamp(seeded, 65 * 60)
    state = AppState()  # nobody online

    # Confirm the seed has at least one non-revoked Rsvp whose player isn't
    # already on the board (the seed has Faulischlumpf in Unassigned but
    # also other RSVP'd users — find one not currently placed).
    from app.db.models import Rsvp
    placed_ids = {
        p.player_id for p in await BoardPlacement.filter(event=seeded["event"])
    }
    rsvps = await Rsvp.filter(
        event=seeded["event"], revoked_at=None,
    ).select_related("player")
    unplaced_rsvps = [r for r in rsvps if r.player_id not in placed_ids]

    n0 = await BoardPlacement.filter(event=seeded["event"]).count()
    await auto_promoter._tick(state, Settings())
    n1 = await BoardPlacement.filter(event=seeded["event"]).count()

    assert n1 == n0 + len(unplaced_rsvps)
    # Subsequent tick is a no-op (guard set).
    await auto_promoter._tick(state, Settings())
    assert await BoardPlacement.filter(event=seeded["event"]).count() == n1
