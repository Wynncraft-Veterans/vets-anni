"""IGN -> UUID resolution that spares the shared Mojang ratelimit bucket.

``api.mojang.com`` is *aggressively* ratelimited and that bucket is shared by
everything on the vets-deploy host (dazebot, temp-server, …). So for the one
case identity resolution actually needs an external lookup — a brand-new,
**non-guild** user's first login — we:

1. **never call ``api.mojang.com``**; and
2. exhaust every free/cached source before any network call at all.

Resolution order (first hit wins; only step 5 touches the network):

1. AppState roster (in-memory, the whole Returners guild) — caller's job,
   handled in ``app.domain.identity.resolve_uuid_cached`` before we're called.
2. AppState legacy-name aliases — likewise.
3. **MojangNameCache** (our DB, write-through, 7-day freshness).
4. **AnniPlayer** — anyone who has logged in before is already keyed by UUID
   with their name; this alone removes ~all repeat lookups.
5. Provider chain, gentle ones first: **PlayerDB** (Nodecraft, no ratelimit)
   → **ashcon** → Mojang's *services* endpoint (``api.minecraftservices.com``,
   a different bucket from ``api.mojang.com``) only as the last resort.

Every successful network resolution is written through to MojangNameCache so
it never costs a second call. dazebot proves the provider set in
``lib/mc/mojang.py``; we reorder it to put the gentle providers first because
we want the *UUID* (stable), not the canonical current name.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from tortoise.expressions import Q

from app.db.models import AnniPlayer, MojangNameCache

logger = logging.getLogger("anni.mojang")

#: PlayerDB asks consumers to send an identifying UA so they can reach out.
_USER_AGENT = "vets-anni (+https://wynnvets.org)"
#: Cached UUID is reused this long before we re-resolve (names change rarely;
#: a stale cache only matters for the non-guild case, and is self-healing).
_CACHE_MAX_AGE = timedelta(days=7)

_session: aiohttp.ClientSession | None = None
#: One lock per lowercased name so concurrent logins of the same person make
#: a single upstream call, not N.
_locks: dict[str, asyncio.Lock] = {}


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        )
    return _session


async def close() -> None:
    """Close the shared session (called from the app lifespan)."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


def _dash(raw: str) -> str:
    """Normalise a 32-hex id to dashed 8-4-4-4-12 (passthrough otherwise)."""
    s = raw.strip().replace("-", "")
    if len(s) != 32:
        return raw.strip()
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"


# --- free/cached steps (no api.mojang.com, steps 3-4) ----------------------
async def _from_name_cache(ign: str) -> str | None:
    row = (
        await MojangNameCache.filter(username__iexact=ign)
        .order_by("-refreshed_at")
        .first()
    )
    if row is None:
        return None
    age = datetime.now(timezone.utc) - row.refreshed_at
    if age > _CACHE_MAX_AGE:
        logger.debug("mojang: %s in name-cache but stale (%s) — will re-resolve", ign, age)
        return None
    logger.debug("mojang: %s resolved from MojangNameCache -> %s", ign, row.mc_uuid)
    return row.mc_uuid


async def _from_anni_player(ign: str) -> str | None:
    player = await AnniPlayer.filter(
        Q(mc_username__iexact=ign) | Q(wynn_username__iexact=ign)
    ).first()
    if player is None:
        return None
    logger.debug("mojang: %s resolved from a known AnniPlayer -> %s", ign, player.mc_uuid)
    return player.mc_uuid


# --- network providers (gentle first; api.mojang.com NEVER used) -----------
async def _try_playerdb(ign: str) -> str | None:
    session = await _get_session()
    try:
        async with session.get(
            f"https://playerdb.co/api/player/minecraft/{ign}"
        ) as res:
            if res.status != 200:
                logger.debug("playerdb %s for %s", res.status, ign)
                return None
            data = await res.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("playerdb lookup failed for %s: %s", ign, exc)
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    player = (data.get("data") or {}).get("player") or {}
    raw = player.get("id") or player.get("raw_id")
    return _dash(str(raw)) if raw else None


async def _try_ashcon(ign: str) -> str | None:
    session = await _get_session()
    try:
        async with session.get(
            f"https://api.ashcon.app/mojang/v2/user/{ign}"
        ) as res:
            if res.status != 200:
                logger.debug("ashcon %s for %s", res.status, ign)
                return None
            data = await res.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("ashcon lookup failed for %s: %s", ign, exc)
        return None
    uuid = data.get("uuid") if isinstance(data, dict) else None
    return _dash(str(uuid)) if uuid else None


async def _try_mojang_services(ign: str) -> str | None:
    """Mojang's *services* host — a DIFFERENT bucket from api.mojang.com.
    Last resort only; still not the aggressively-limited legacy endpoint."""
    session = await _get_session()
    try:
        async with session.get(
            f"https://api.minecraftservices.com/minecraft/profile/lookup/name/{ign}"
        ) as res:
            if res.status != 200:
                logger.debug("minecraftservices %s for %s", res.status, ign)
                return None
            data = await res.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("minecraftservices lookup failed for %s: %s", ign, exc)
        return None
    raw = data.get("id") if isinstance(data, dict) else None
    return _dash(str(raw)) if raw else None


async def username_to_uuid(ign: str) -> str | None:
    """Resolve ``ign`` -> dashed UUID, cheapest source first; ``None`` if
    unknown everywhere. Never raises (login surfaces a friendly error) and
    never touches ``api.mojang.com``."""
    ign = (ign or "").strip()
    if not ign:
        return None

    lock = _locks.setdefault(ign.lower(), asyncio.Lock())
    async with lock:
        # Steps 3-4: free DB sources.
        for src in (_from_name_cache, _from_anni_player):
            uuid = await src(ign)
            if uuid:
                return uuid

        # Step 5: gentle providers first; Mojang's services host last.
        for name, fn in (
            ("playerdb", _try_playerdb),
            ("ashcon", _try_ashcon),
            ("minecraftservices", _try_mojang_services),
        ):
            uuid = await fn(ign)
            if uuid:
                logger.debug("mojang: %s resolved via %s -> %s", ign, name, uuid)
                # Write-through so this name never costs a second call.
                await MojangNameCache.update_or_create(
                    mc_uuid=uuid, defaults={"username": ign}
                )
                return uuid

        logger.info("mojang: could not resolve IGN %r via any source", ign)
        return None
