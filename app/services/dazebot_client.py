"""Client for dazebot's secret-gated ``/api/internal/anni-identity`` endpoint.

fishbot needs to know "who is this Discord user in Minecraft terms" to write
an :class:`Rsvp` row, but dazebot owns the Discord<->MC link. Per
``.claude/integration.md`` we POST the Discord snowflake here and dazebot
replies with the linked identity + membership tier (the same
``resolve_tier`` it uses for its own commands).

Auth is the shared ``X-Introspect-Secret`` header (fail-closed). The endpoint
is only reachable on the private ``verify`` Docker network — dazebot
explicitly returns 503 when the secret is unset on its side, which we
surface as a graceful "service unavailable" rather than a crash.

Mirrors :mod:`app.services.tempserver` for shape: a singleton client with a
lazy ``aiohttp.ClientSession`` and an :func:`close` for the lifespan teardown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from app.settings import get_settings

logger = logging.getLogger("anni.dazebot_client")


@dataclass(frozen=True)
class AnniIdentity:
    """Parsed response from ``POST /api/internal/anni-identity``.

    ``linked`` is ``False`` when dazebot couldn't find the Discord user in any
    of its guilds OR found them but they have no MC link; ``mc_uuid`` /
    ``mc_username`` / ``tier`` are then ``None`` and ``reason`` carries the
    human-readable hint dazebot returned.
    """

    linked: bool
    disc_uuid: str
    mc_uuid: str | None
    mc_username: str | None
    tier: str | None  # "member" | "waitlist" | "honourary" | "other" | None
    blocked: bool
    reason: str | None


class DazebotClient:
    """Thin async wrapper. One shared session; construct via
    :func:`get_dazebot_client`."""

    def __init__(self) -> None:
        settings = get_settings()
        self._url = settings.dazebot_anni_identity_url
        self._secret = settings.dazebot_introspect_secret
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "vets-anni/fishbot"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def resolve_anni_identity(
        self, discord_id: int | str
    ) -> AnniIdentity | None:
        """POST the snowflake; ``None`` on transport/HTTP/secret failure.

        The cog converts ``None`` into a friendly "service unavailable" reply
        so dazebot being down never crashes a ``/rsvp`` invocation. A
        successful HTTP response with ``linked=False`` is *not* an error —
        that comes back as a populated :class:`AnniIdentity` and the cog
        renders the "link your account first" branch.
        """
        if not self._secret:
            logger.warning(
                "dazebot_client: DAZEBOT_INTROSPECT_SECRET unset; /rsvp will "
                "degrade gracefully (service unavailable)."
            )
            return None
        body = {"discord_id": str(discord_id)}
        try:
            session = await self._get_session()
            async with session.post(
                self._url, json=body,
                headers={"X-Introspect-Secret": self._secret},
            ) as res:
                if res.status != 200:
                    logger.warning(
                        "dazebot_client: %s -> HTTP %d", self._url, res.status,
                    )
                    return None
                data = await res.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("dazebot_client: %s unreachable: %s", self._url, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("dazebot_client: non-object body %r", type(data))
            return None
        return AnniIdentity(
            linked=bool(data.get("linked")),
            disc_uuid=str(data.get("disc_uuid") or ""),
            mc_uuid=data.get("mc_uuid"),
            mc_username=data.get("mc_username"),
            tier=data.get("tier"),
            blocked=bool(data.get("blocked")),
            reason=data.get("reason"),
        )


_client: DazebotClient | None = None


def get_dazebot_client() -> DazebotClient:
    """Process-wide singleton (lazily constructed)."""
    global _client
    if _client is None:
        _client = DazebotClient()
    return _client


async def close() -> None:
    """Lifespan-shutdown hook (mirrors :mod:`app.services.tempserver`)."""
    if _client is not None:
        await _client.close()
