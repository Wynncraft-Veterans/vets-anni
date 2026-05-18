"""Wynncraft API client — vets-anni's OWN token, OWN ratelimit bucket.

This is the *only* place ``WAPI_TOKEN`` is sent (hard rule, see
``.claude/integration.md``). It is a lean port of dazebot's
``lib/mc/wynn_api/requestor.py``: one shared :class:`aiohttp.ClientSession`,
a single background worker draining a priority queue (so a slow weapons-catalog
crawl never blocks a login's player lookup), ``RateLimit-*`` header throttling,
and 429 back-off honouring ``Retry-After`` / ``RateLimit-Reset``.

We deliberately spend the token on only three things:
* ``/v3/guild/{name}``           — guild online members (online-merge),
* ``/v3/item/search/{query}``    — the weapons catalog (1 h cache),
* ``/v3/player/{uuid|name}``     — login guild/identity + Phase-2 probe.

Everything heavy (staff, roster, aliases) comes from api.wynnvets.org, which
already paid the WAPI cost — see ``app/services/tempserver.py``.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Any

import aiohttp

from app.settings import get_settings

logger = logging.getLogger("anni.wapi")

#: Queue priorities (lower = sooner). Interactive lookups must not sit behind
#: the hourly weapons crawl.
PRIO_HIGH = 0   # login/identity player lookups
PRIO_NORMAL = 5
PRIO_LOW = 9    # background catalog / probe crawls


class WapiError(RuntimeError):
    """Raised for a non-retryable upstream failure (4xx other than 429)."""


class WapiClient:
    """Singleton-style async WAPI client. Construct via :func:`get_wapi`.

    Usage::

        data = await get_wapi().get_json("player/<uuid>", priority=PRIO_HIGH)

    Returns the decoded JSON ``dict`` (or raises :class:`WapiError`). The
    worker task is started lazily on first request and stopped via
    :meth:`close` from the app lifespan.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session: aiohttp.ClientSession | None = None
        self._queue: asyncio.PriorityQueue[tuple] | None = None
        self._worker: asyncio.Task | None = None
        self._seq = itertools.count()  # FIFO tiebreaker within a priority
        # Ratelimit gate: never fire before this monotonic time.
        self._not_before = 0.0

    # --- lifecycle -----------------------------------------------------------
    async def _ensure_worker(self) -> asyncio.PriorityQueue:
        if self._queue is None:
            self._queue = asyncio.PriorityQueue()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="wapi-worker")
        return self._queue

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            token = self._settings.wapi_token
            headers = {"User-Agent": "vets-anni"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                # No token => unauthenticated bucket (tiny ratelimit). Fine for
                # local dev; production always sets WAPI_TOKEN.
                logger.warning("WAPI_TOKEN unset — using the anonymous ratelimit bucket")
            self._session = aiohttp.ClientSession(
                base_url=self._settings.wapi_base.rstrip("/").removesuffix("/v3") or None,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass
            self._worker = None
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # --- public API ----------------------------------------------------------
    async def get_json(self, path: str, *, priority: int = PRIO_NORMAL) -> Any:
        """GET ``/v3/{path}`` and return decoded JSON (dict *or* list).

        Blocks until the worker services this request (FIFO within a
        priority). Raises :class:`WapiError` on a hard failure; the caller
        (a poller / route) is expected to catch and fall back to last-good.
        """
        return await self._enqueue("GET", path, None, priority)

    async def post_json(
        self, path: str, body: Any, *, priority: int = PRIO_NORMAL
    ) -> Any:
        """POST ``body`` as JSON to ``/v3/{path}`` and return decoded JSON.

        Used for the v3 advanced item search (``POST /v3/item/search`` with a
        ``{"type": [...]}`` filter) — the only correct way to *enumerate* the
        weapon catalog; ``GET /v3/item/search/{q}`` is a name search.
        """
        return await self._enqueue("POST", path, body, priority)

    async def _enqueue(self, method: str, path: str, body: Any, priority: int) -> Any:
        queue = await self._ensure_worker()
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        # seq (unique, monotonic) is the priority tiebreaker, so the trailing
        # request fields are never compared by the PriorityQueue.
        await queue.put((priority, next(self._seq), method, path, body, fut))
        return await fut

    # --- worker --------------------------------------------------------------
    async def _run(self) -> None:
        """Drain the priority queue one request at a time, honouring limits."""
        assert self._queue is not None
        while True:
            priority, _seq, method, path, body, fut = await self._queue.get()
            try:
                result = await self._do(method, path, body)
                if not fut.done():
                    fut.set_result(result)
            except asyncio.CancelledError:
                if not fut.done():
                    fut.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 - surface to the awaiter
                if not fut.done():
                    fut.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _do(
        self, method: str, path: str, body: Any, _retries: int = 3
    ) -> Any:
        # Respect the ratelimit gate learned from the previous response.
        delay = self._not_before - time.monotonic()
        if delay > 0:
            logger.debug("WAPI gate: holding %.1fs before /v3/%s", delay, path)
            await asyncio.sleep(delay)

        session = await self._get_session()
        url = f"/v3/{path.lstrip('/')}"
        logger.debug("WAPI %s %s", method, url)
        ctx = (
            session.post(url, json=body)
            if method == "POST"
            else session.get(url)
        )
        async with ctx as res:
            self._update_ratelimit(res.headers)
            logger.debug("WAPI %s %s -> %d", method, url, res.status)
            if res.status == 429:
                retry = _header_seconds(res.headers, default=2.0)
                logger.warning("WAPI 429 on %s — backing off %.1fs", path, retry)
                self._not_before = time.monotonic() + retry
                if _retries > 0:
                    return await self._do(method, path, body, _retries - 1)
                raise WapiError(f"WAPI ratelimited (429) for {path}")
            if res.status == 404:
                raise WapiError(f"WAPI 404 for {path}")
            if res.status >= 400:
                raise WapiError(f"WAPI {res.status} for {path}")
            # WAPI sometimes serves JSON as text/plain — don't enforce mime.
            return await res.json(content_type=None)

    def _update_ratelimit(self, headers: Any) -> None:
        """Pre-emptively throttle: if we're near the limit, hold until reset.

        Mirrors dazebot's probe — read ``RateLimit-Remaining``/``-Reset`` and,
        when nearly exhausted, push ``_not_before`` to the window reset so the
        *next* request waits instead of eating a 429.
        """
        try:
            remaining = int(float(headers.get("RateLimit-Remaining", "999")))
        except (TypeError, ValueError):
            remaining = 999
        if remaining <= 1:
            reset = _header_seconds(headers, default=1.0)
            self._not_before = max(self._not_before, time.monotonic() + reset)


def _header_seconds(headers: Any, *, default: float) -> float:
    """Seconds to wait, from ``RateLimit-Reset`` then ``Retry-After``."""
    for key in ("RateLimit-Reset", "Retry-After"):
        val = headers.get(key)
        if val is not None:
            try:
                return max(0.0, float(val))
            except ValueError:
                continue
    return default


_client: WapiClient | None = None


def get_wapi() -> WapiClient:
    """Process-wide WAPI client singleton (lazily constructed)."""
    global _client
    if _client is None:
        _client = WapiClient()
    return _client
