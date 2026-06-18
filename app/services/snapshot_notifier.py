"""Fire-and-forget delta notifier for the anni snapshot push pipeline.

Whenever a Tortoise ``post_save`` (or explicit signal-bypass call site) fires
for a snapshot-relevant write, the handler in
:mod:`app.services.snapshot_signal_handlers` calls
:meth:`SnapshotNotifier.notify_uuids` (or :meth:`notify_all`) to POST a JSON
payload to temp-server's ``/api/internal/anni-snapshot-delta`` endpoint.
Temp-server then refetches snapshots for the named UUIDs from vets-anni's
existing ``/api/internal/anni-snapshot-batch`` and pushes ``anni_state`` WS
frames to the matching vetsmod sessions — sub-50 ms end-to-end on the happy
path, vs. the 10 s poll the safety net would have taken.

Fire-and-forget semantics: every public method schedules an
:func:`asyncio.create_task` and returns immediately. The save path NEVER
awaits HTTP — signal handlers must not block the DB transaction. POST
errors are log-and-drop; the polling safety net on temp-server covers
eventual consistency.

Fail-closed when ``temp_server_delta_secret`` is empty: the notifier
becomes a no-op + logs a one-time warning at first call. That keeps dev
boots clean without forcing every developer to set a secret locally.

Same lazy-aiohttp / singleton pattern as
:class:`app.services.tempserver.TempServerClient` and
:class:`app.services.dazebot_client.DazebotClient` — one shared session,
``close()`` for lifespan teardown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import aiohttp

from app.settings import get_settings

logger = logging.getLogger("anni.snapshot_notifier")

# Total HTTP timeout for one delta POST. Temp-server's downstream batch
# fetch can take a second or two for large fanouts; we wait long enough
# to surface 4xx/5xx in logs but not so long that a stuck temp-server
# pins the notifier task indefinitely.
_TIMEOUT_SECONDS = 5.0


class SnapshotNotifier:
    """Thin async POSTer. One shared session; construct via :func:`get`."""

    def __init__(self) -> None:
        settings = get_settings()
        self._url = settings.temp_server_delta_url
        self._secret = settings.temp_server_delta_secret
        self._session: aiohttp.ClientSession | None = None
        self._warned_no_secret = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "vets-anni-snapshot-notifier"},
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # --- public API ----------------------------------------------------------
    def notify_uuids(self, uuids: Iterable[str]) -> None:
        """Schedule a POST for the given UUIDs.

        De-dupes, drops empties, never raises. The HTTP call runs on a
        background task — the caller (a DB signal handler) returns
        immediately and the save path is never delayed.
        """
        cleaned = sorted({str(u) for u in uuids if u})
        if not cleaned:
            return
        asyncio.create_task(self._post({"uuids": cleaned}))

    def notify_all(self) -> None:
        """Schedule a "refresh every subscribed user" POST.

        Used by mass-fanout sites where enumerating affected UUIDs would
        be wasteful — grace-wipe (hundreds of rows touched in one
        transaction) and AnniEvent activation (every subscribed user's
        snapshot may have shifted).
        """
        asyncio.create_task(self._post({"all": True}))

    # --- internals -----------------------------------------------------------
    async def _post(self, body: dict) -> None:
        if not self._secret:
            if not self._warned_no_secret:
                self._warned_no_secret = True
                logger.warning(
                    "snapshot_notifier: TEMP_SERVER_DELTA_SECRET unset — "
                    "running as no-op. Polling safety net on temp-server "
                    "continues to cover eventual consistency."
                )
            return
        try:
            session = await self._get_session()
            async with session.post(
                self._url,
                json=body,
                headers={"X-Introspect-Secret": self._secret},
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    logger.warning(
                        "snapshot_notifier: temp-server %d: %.200s",
                        response.status,
                        text,
                    )
                    return
            # Quick observability — gap between push events is also visible
            # on the temp-server side; here we log size only.
            if "uuids" in body:
                logger.debug(
                    "snapshot_notifier: posted %d uuids", len(body["uuids"])
                )
            else:
                logger.debug("snapshot_notifier: posted notify_all")
        except asyncio.TimeoutError:
            logger.warning("snapshot_notifier: timeout posting to temp-server")
        except aiohttp.ClientError as exc:
            logger.warning("snapshot_notifier: client error: %s", exc)
        except Exception:
            # Defensive: signal handlers must never see an exception bubble
            # up from here. Polling safety net covers the missed delta.
            logger.exception("snapshot_notifier: unexpected error")


_client: SnapshotNotifier | None = None


def get() -> SnapshotNotifier:
    """Process-wide snapshot-notifier singleton (lazily constructed)."""
    global _client
    if _client is None:
        _client = SnapshotNotifier()
    return _client
