"""stamp_poller — the anni clock.

Reads ``/v1/outbound/stamp`` (the single source for every countdown) and
drives the :class:`AnniEvent` row:

* **future stamp**         -> ensure exactly one active event with that epoch
  (create it, or update the existing active event's epoch on a
  re-announcement — never a duplicate).
* **empty / past stamp**   -> idle. Phase 1 deliberately never *deletes*: the
  now>stamp grace window and the >stamp+2h wipe are Phase 2's
  ``lifecycle_task``. Keeping a just-past event active is correct (it's mid
  grace); a stale event is rotated out when the next future stamp arrives.
"""

from __future__ import annotations

import logging
import time

from tortoise.transactions import in_transaction

from app.db.lifecycle import ensure_single_active, get_active_event
from app.db.models import AnniEvent
from app.services.loop import poll_forever
from app.services.state import AppState
from app.services.tempserver import get_tempserver
from app.settings import Settings

logger = logging.getLogger("anni.stamp")


async def _tick(state: AppState, settings: Settings) -> None:
    epoch = await get_tempserver().stamp()
    state.stamp_epoch = epoch
    state.touch("stamp_fetched_at")

    if epoch is None:
        logger.debug("stamp tick: none announced")
        return
    now = int(time.time())
    if epoch <= now:
        # Past/now: an existing active event is mid-grace (Phase 2 wipes it);
        # if none is active there is simply nothing to announce yet.
        logger.debug("stamp tick: %d is past/now (%+ds) — idle", epoch, epoch - now)
        return
    logger.debug("stamp tick: %d is %d s out", epoch, epoch - now)

    # Future stamp: there must be exactly one active event carrying it.
    active = await get_active_event()
    if active is not None and active.wiped_at is None:
        if active.stamp_epoch != epoch:
            active.stamp_epoch = epoch
            await active.save(update_fields=["stamp_epoch"])
            logger.info("stamp re-announced -> updated active event to %d", epoch)
        return

    async with in_transaction():
        event = await AnniEvent.create(stamp_epoch=epoch, is_active=True)
        await ensure_single_active(event)
    logger.info("new anni announced (stamp=%d) -> created active event", epoch)


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "stamp",
        lambda: settings.stamp_poll_seconds,
        lambda: _tick(state, settings),
    )
