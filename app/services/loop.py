"""The shared poller loop skeleton.

Copied in spirit from temporary-server ``app/services/*``: an async ``while
True`` that ticks, swallows-and-logs any non-cancellation exception (a bad
tick must never kill the loop — last-good cache stays served), then sleeps.

Cadence is read fresh each iteration via a callable so an ``AppConfig``
runtime override (Phase 2) takes effect without a redeploy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("anni.poller")


async def poll_forever(
    name: str,
    interval: Callable[[], float],
    tick: Callable[[], Awaitable[None]],
) -> None:
    """Run ``tick`` every ``interval()`` seconds until cancelled.

    ``name`` is only used for logging. The first tick runs immediately so the
    cache is warm as soon as possible after boot.
    """
    logger.info("%s poller started", name)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            logger.info("%s poller stopped", name)
            raise
        except Exception:  # noqa: BLE001 - resilience is the whole point
            logger.exception("%s poller tick failed (serving last-good)", name)
        await asyncio.sleep(max(1.0, interval()))
