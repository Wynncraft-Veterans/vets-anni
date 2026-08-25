"""auto_promoter — lands online players + outstanding RSVPs on the board.

Two trigger surfaces feed the **Unassigned** bucket:

1. The fishbot ``/rsvp`` cog calls ``buckets.ensure_placed`` directly the
   moment a user RSVPs (immediate UI feedback for the user).
2. *This* poller — from ``T - hot_window_open_seconds`` (default 70 min)
   through ``stamp + grace`` it scans the online-merge cache and lands any
   guild member who is online but not yet on the board. Closes the spec.md
   line "auto-populated from RSVP or 1hr-early".

The poller also performs a **one-shot boot heal** on its first tick after
process start: every non-revoked Rsvp for the active event is fed through
``ensure_placed``. That covers RSVPs accepted while the process was down (or
those that pre-date this feature) without a manual backfill script.

**Two stored Unassigned lanes**:

* **main** — RSVP'd users (boot-heal sweep, and any RSVP'd person the
  auto-promoter catches online). Lane is decided at insert time, so a
  walk-in who later RSVPs stays in walk-in until staff moves them.
* **walk-in** — online players with no RSVP at all. Keyed off the RSVP,
  not the clock: the lane means "never declared".

(A third, *soft RSVP*, is derived per render in ``web/board_view`` and has
no flag here. There is no LATE lane: lateness is a per-person badge whose
threshold slides with the RSVP — see ``constants.LATE_ARRIVAL_SECONDS`` —
and ``buckets.ensure_placed`` stamps it at insert.)

Online UUIDs without an ``AnniPlayer`` row (e.g. a guild member who has
never opened the dashboard) get a *placeholder* row — empty stats, an
``is_placeholder=True`` flag the board renders as a stub card. The flag is
cleared on first meaningful interaction by ``domain.identity.mark_registered``.

Grace bypass: ``ensure_placed`` writes directly via ``domain.buckets._upsert``
and does not go through ``board_hub`` 's grace freeze — that freeze blocks
*organiser-driven* drag/drop, not system-driven new-arrival inserts.
"""

from __future__ import annotations

import logging

from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer, Rsvp
from app.domain import buckets
from app.services import hot_window
from app.services.loop import poll_forever
from app.services.state import AppState
from app.settings import Settings

logger = logging.getLogger("anni.autoprom")

#: One-shot guard for the RSVP boot-heal sweep. Module-level so a successful
#: sweep survives across ticks but a process restart re-runs it.
_swept: bool = False


async def _tick(state: AppState, settings: Settings) -> None:
    global _swept

    event = await get_active_event()
    if event is None:
        hot_window.set_currently_hot(False)
        return

    grace_seconds = max(0, settings.grace_hours) * 3600
    hot = hot_window.is_hot(
        event,
        hot_window_open_seconds=settings.hot_window_open_seconds,
        grace_seconds=grace_seconds,
    )
    # Publish the flag so online_merge / presence_poller pickers can ramp
    # alongside us without each maintaining its own active-event query.
    hot_window.set_currently_hot(hot)
    if not hot:
        return

    inserted_any = False

    # Cache the active-RSVP UUID set once per tick; the auto-add loop below
    # uses it to route non-RSVP'd online users into the walk-in sub-bucket
    # while RSVP'd users land in the main lane. Whether any given arrival is
    # LATE is decided inside ``ensure_placed`` (it slides with their RSVP);
    # this loop only picks the lane.
    rsvped_uuids: set[str] = set(
        await Rsvp.filter(event=event, revoked_at=None)
        .values_list("player_id", flat=True)
    )

    # --- (1) boot heal: outstanding RSVPs on first hot tick after start.
    # RSVP'd users always land in the main lane (never walk-in).
    if not _swept:
        rsvps = (
            await Rsvp.filter(event=event, revoked_at=None)
            .select_related("player")
        )
        for r in rsvps:
            if await buckets.ensure_placed(event, r.player, is_walkin=False):
                inserted_any = True
        _swept = True
        logger.info(
            "boot-heal swept %d non-revoked RSVPs (inserted=%s)",
            len(rsvps), inserted_any,
        )

    # --- (2) the actual 1hr-early auto-add.
    online_uuids = set(state.online_by_uuid.keys()) | set(state.api_active_uuids)
    for uuid in online_uuids:
        op = state.online_by_uuid.get(uuid)
        fallback_name = op.username if op else uuid[:8]
        player, created = await AnniPlayer.get_or_create(
            mc_uuid=uuid,
            defaults={
                "mc_username": fallback_name,
                "is_placeholder": True,
            },
        )
        # NEVER re-flip an existing player to placeholder — the flag only
        # ever flows True → False.
        # The walk-in lane means "never declared", so it is keyed off the
        # RSVP alone — not the clock.
        is_walkin = uuid not in rsvped_uuids
        if await buckets.ensure_placed(event, player, is_walkin=is_walkin):
            inserted_any = True

    if not inserted_any:
        logger.debug(
            "auto-promoter tick: %d online (%d RSVP'd), no new placements",
            len(online_uuids), len(rsvped_uuids & online_uuids),
        )
        return

    # Lazy import keeps the services layer free of any web import at module
    # load (the hub itself is FastAPI-free; this is the same shape used by
    # presence_poller for its broadcast).
    from app.web.ws.board_hub import get_board_hub

    hub = get_board_hub()
    await hub.broadcast_snapshot(event, state)
    logger.info(
        "auto-promoter broadcast snapshot (%d ws clients)",
        hub.client_count,
    )


def _pick_interval(settings: Settings) -> float:
    """Hot cadence iff the last tick saw the hot window; idle otherwise.

    Reads ``settings`` fresh each call so a runtime knob change is picked
    up without a restart (matches the other pollers' pattern).
    """
    return float(
        settings.auto_promoter_hot_seconds
        if hot_window.is_currently_hot()
        else settings.auto_promoter_seconds
    )


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "autopromoter",
        lambda: _pick_interval(settings),
        lambda: _tick(state, settings),
    )
