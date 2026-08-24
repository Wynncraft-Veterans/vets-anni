"""lifecycle_task — owns the grace-open and the 2 h grace-wipe.

``stamp_poller`` deliberately only ever *creates/updates* the active event for
a future stamp (it never deletes); this task owns the back half of the
lifecycle so the destructive transition lives in exactly one place:

* **GRACE opens** (now > stamp): stamp the event's ``grace_opened_at`` once
  (audit/display). The board going read-only is enforced live by
  ``board_hub`` via ``domain/schedule`` — not a flag we set here, so a clock
  skew can't strand the board frozen.
* **WIPE** (now > stamp + ``grace_hours``): in **one transaction** — snapshot
  per-party results, bump ``success_count`` for every core role a WIN *party*
  member was assigned — bucketed players (Unassigned/Volunteers/Sitting-out)
  never earn credit, assigned role or not (parties left ``TBD`` at wipe time
  are treated as WIN —
  staff had the whole grace window to mark a ``LOSS``/``LAG`` and didn't, so
  the members still get credit), delete this event's
  ``BoardPlacement``/``Rsvp``, mark ``wiped_at`` + ``is_active=False`` — then
  broadcast ``BOARD_WIPE``. ``RoleCapability``/``AnniPlayer`` persist; a later
  future stamp makes a fresh event via ``stamp_poller`` (a re-announce
  updates, never duplicates).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from tortoise.expressions import F
from tortoise.transactions import in_transaction

from app.constants import CAPABILITY_ROLES, PartyResult
from app.db.lifecycle import get_active_event
from app.db.models import BoardPlacement, Party, RoleCapability, Rsvp
from app.domain.schedule import EventPhase, phase_of
from app.services.loop import poll_forever
from app.services.state import AppState
from app.settings import Settings

logger = logging.getLogger("anni.lifecycle")


async def _credit_wins(event) -> int:
    """+1 ``success_count`` for each (player, core-role) a WIN party member was
    assigned this event. Returns the number of capabilities credited.

    ``TBD`` (unset) parties are credited too: staff had the full grace window
    to record a ``LOSS``/``LAG`` and didn't, so we default the missing result
    to WIN rather than silently deny credit to players who did their part.
    Explicit ``LOSS``/``LAG`` still get no credit.

    **Only players who actually sat in a party are credited.** Anyone left in
    a bucket — Unassigned, Volunteers, Sitting-out — gets nothing, *even if
    they carry an assigned role*: a role can be set on a card long before (or
    after) it is dragged into a party, so ``assigned_role`` on a bucketed
    placement records an intent that never happened. The ``party_id`` /
    ``bucket`` predicates are what enforce that, and they are spelled out
    rather than left to the ``party__result__in`` join: that join *happens*
    to drop bucketed rows today (a LEFT JOIN makes ``NULL IN (...)`` false),
    but win-credit is exactly the kind of rule that should not rest on SQL
    three-valued-logic trivia.

    One query for the (event × credited-party × core-role) cross-join — FILL /
    role-less placements are excluded by the ``assigned_role__in`` filter so
    they never reach the per-row capability update."""
    members = (
        await BoardPlacement.filter(
            event=event,
            party_id__isnull=False,
            bucket__isnull=True,
            party__result__in=(PartyResult.WIN, PartyResult.TBD),
            assigned_role__in=CAPABILITY_ROLES,
        )
        .select_related("player")
    )
    credited = 0
    for m in members:
        updated = await RoleCapability.filter(
            player=m.player, role=m.assigned_role,
        ).update(success_count=F("success_count") + 1)
        credited += updated
    return credited


async def _wipe(event, state: AppState) -> None:
    now = datetime.now(timezone.utc)
    async with in_transaction():
        credited = await _credit_wins(event)
        placements = await BoardPlacement.filter(event=event).count()
        await BoardPlacement.filter(event=event).delete()
        await Party.filter(event=event).update(
            scroll_spot_x=None, scroll_spot_y=None, scroll_spot_z=None,
        )
        await Rsvp.filter(event=event).delete()
        event.wiped_at = now
        event.is_active = False
        await event.save(update_fields=["wiped_at", "is_active"])
    logger.info(
        "anni wiped (stamp=%d): %d placements cleared, %d capabilities "
        "credited (WIN + TBD-default-to-WIN); event marked inactive",
        event.stamp_epoch, placements, credited,
    )
    # The next presence tick recomputes empty (no active event); clear now so
    # nothing stale lingers between ticks.
    state.presence_by_uuid = {}
    state.api_active_uuids = set()
    # Per-event history: without this the next anni would open with everyone
    # who logged in during the last one already reading as OFFLINE_GONE.
    state.seen_online_uuids = set()

    from app.web.ws.board_hub import get_board_hub

    await get_board_hub().broadcast_wipe(None)

    # Mass-fanout snapshot delta. The wipe touches hundreds of rows via three
    # bulk ops (delete BoardPlacement, update Party.scroll_spot, delete Rsvp)
    # plus the per-row credit_wins ``.update()``; none of those fire Tortoise
    # ``post_save``. One ``notify_all`` covers the lot — temp-server refreshes
    # every subscribed UUID via its existing batch fetch path. Fire-and-forget;
    # no await on HTTP so the tick stays bounded.
    from app.services import snapshot_notifier
    snapshot_notifier.get().notify_all()


async def _tick(state: AppState, settings: Settings) -> None:
    event = await get_active_event()
    if event is None or event.wiped_at is not None:
        return

    grace_seconds = max(0, settings.grace_hours) * 3600
    phase = phase_of(event.stamp_epoch, grace_seconds, now=int(time.time()))

    if phase is EventPhase.PENDING:
        return
    if phase is EventPhase.GRACE:
        if event.grace_opened_at is None:
            event.grace_opened_at = datetime.now(timezone.utc)
            await event.save(update_fields=["grace_opened_at"])
            logger.info("grace opened for anni (stamp=%d) — board read-only "
                        "except party result/stage", event.stamp_epoch)
        return
    # EXPIRED
    await _wipe(event, state)


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "lifecycle",
        lambda: settings.lifecycle_poll_seconds,
        lambda: _tick(state, settings),
    )
