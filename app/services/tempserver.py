"""Client for api.wynnvets.org ``/v1/outbound/*`` (read-only, no auth).

temporary-server already paid the WAPI cost for these, so we read the heavy
data (staff, the online list, roster, aliases) from here and spend our OWN
token only on the three things in ``app/services/wapi.py``.

Contract (see ``.claude/integration.md`` + temporary-server
``app/routes/static.py``):

* ``GET /v1/outbound/stamp``   -> plain text unix epoch ("" when none).
* ``GET /v1/outbound/staff``   -> ``[{uuid,username,rank,online,server}]``.
* ``GET /v1/outbound/list``    -> ``{"connected":[{uuid,username,tier,queued,server}]}``.
* ``GET /v1/outbound/roster``  -> ``{uuid: username}``.
* ``GET /v1/outbound/aliases`` -> ``{legacyname_lower: uuid}``.

Every method returns a *typed, already-defaulted* value and raises only on a
genuine transport/HTTP error so callers can cleanly fall back to last-good.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from app.settings import get_settings

logger = logging.getLogger("anni.tempserver")


class TempServerClient:
    """Thin async wrapper. One shared session; construct via :func:`get_tempserver`."""

    def __init__(self) -> None:
        self._base = get_settings().vets_api_base.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "vets-anni"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get(self, path: str) -> aiohttp.ClientResponse:
        session = await self._get_session()
        return await session.get(f"{self._base}/v1/outbound/{path}")

    # --- endpoints -----------------------------------------------------------
    async def stamp(self) -> int | None:
        """Latest announced anni epoch, or ``None`` (empty/unparseable).

        The source is plain text. Empty body, whitespace, or a non-integer
        all mean "nothing announced" — never raise for that, only for
        transport errors.
        """
        async with await self._get("stamp") as res:
            res.raise_for_status()
            body = (await res.text()).strip()
        if not body:
            logger.debug("tempserver stamp: empty (no anni announced)")
            return None
        try:
            epoch = int(float(body))
        except ValueError:
            logger.warning("stamp: non-numeric body %r — treating as none", body[:32])
            return None
        logger.debug("tempserver stamp: %d", epoch)
        return epoch

    async def staff(self) -> list[dict[str, Any]]:
        """Online staff only: ``[{uuid,username,rank,online,server}]``."""
        async with await self._get("staff") as res:
            res.raise_for_status()
            data = await res.json()
        rows = data if isinstance(data, list) else []
        logger.debug("tempserver staff: %d online", len(rows))
        return rows

    async def online_list(self) -> list[dict[str, Any]]:
        """vetsmod-connected clients: ``[{uuid,username,tier,queued,server}]``.

        ``server`` is the current world (e.g. ``"EU15"``) joined server-side
        from the latest tablist snapshot, or ``null`` when no fresh tablist
        covers the player. Unwraps the ``{"connected": [...]}`` envelope.
        """
        async with await self._get("list") as res:
            res.raise_for_status()
            data = await res.json()
        rows = data.get("connected", []) if isinstance(data, dict) else []
        rows = rows if isinstance(rows, list) else []
        logger.debug(
            "tempserver list: %d connected (%d queued)",
            len(rows),
            sum(1 for r in rows if r.get("queued")),
        )
        return rows

    async def roster(self) -> dict[str, str]:
        """Authoritative ``{uuid: username}`` for the Returners guild."""
        async with await self._get("roster") as res:
            res.raise_for_status()
            data = await res.json()
        roster = data if isinstance(data, dict) else {}
        logger.debug("tempserver roster: %d members", len(roster))
        return roster

    async def aliases(self) -> dict[str, str]:
        """``{legacyname_lower: uuid}`` for rename-desync resolution."""
        async with await self._get("aliases") as res:
            res.raise_for_status()
            data = await res.json()
        aliases = data if isinstance(data, dict) else {}
        logger.debug("tempserver aliases: %d legacy names", len(aliases))
        return aliases


_client: TempServerClient | None = None


def get_tempserver() -> TempServerClient:
    """Process-wide tempserver client singleton (lazily constructed)."""
    global _client
    if _client is None:
        _client = TempServerClient()
    return _client
