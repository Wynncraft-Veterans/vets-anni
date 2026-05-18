"""Identity resolution — IGN -> Minecraft UUID (the anchor) + profile.

The whole system keys on ``mc_uuid`` (hard rule). Resolution order, cheapest
first, exactly mirroring how vetsmod resolves names so the two stay in sync:

1. the cached authoritative **roster** (uuid->name) — current names;
2. the **aliases** map (legacyname_lower->uuid) — handles offline renames;
3. **Mojang** as a last resort (then cached).

The pure cache step is split out (``resolve_uuid_cached``) so identity
resolution is unit-testable with plain dicts and an injected Mojang stub — no
network. The async wrapper just wires the real callable in.

``mc_username`` = the authoritative *current* name (roster/Mojang);
``wynn_username`` = the possibly-stale in-game name from WAPI. When they
differ the UI shows ``wynn|mc`` (rename desync, dazebot's convention).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.constants import API_DISABLED_LAST_ONLINE_MAX
from app.services.state import AppState
from app.services.wapi import PRIO_HIGH, WapiError, get_wapi

logger = logging.getLogger("anni.identity")

UTC = timezone.utc
#: API-disabled / unknown last-online sentinel (dazebot's convention).
EPOCH = datetime.fromtimestamp(0, tz=UTC)

MojangResolver = Callable[[str], Awaitable[str | None]]


def is_api_disabled(dt: datetime | None) -> bool:
    """True when ``last_online`` is the epoch sentinel (API hidden / unknown).

    ``None`` counts as disabled. Anything within
    ``API_DISABLED_LAST_ONLINE_MAX`` seconds of the epoch is the sentinel —
    tolerates tz round-trip drift around 1970 (mirrors dazebot
    ``is_last_online_unknown``).
    """
    if dt is None:
        return True
    return dt.timestamp() <= API_DISABLED_LAST_ONLINE_MAX


def dash_uuid(raw: str) -> str:
    """Normalise a 32-hex Mojang id to canonical 8-4-4-4-12 dashed form.

    Already-dashed input is returned unchanged.
    """
    s = raw.strip()
    if "-" in s or len(s) != 32:
        return s
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"


def resolve_uuid_cached(
    ign: str, roster_by_uuid: dict[str, str], aliases: dict[str, str]
) -> str | None:
    """Pure cache resolution: roster (current names) then legacy aliases.

    Case-insensitive. Returns ``None`` when neither cache knows ``ign`` (the
    caller then falls back to Mojang).
    """
    if not ign:
        return None
    needle = ign.strip().lower()
    for uuid, name in roster_by_uuid.items():
        if name.lower() == needle:
            return uuid
    return aliases.get(needle)


async def mojang_username_to_uuid(ign: str) -> str | None:
    """Default last-resort resolver: ``ign`` -> dashed UUID, or ``None``.

    Delegates to :func:`app.services.mojang.username_to_uuid`, which exhausts
    the DB caches (MojangNameCache, then any known AnniPlayer) before any
    network call and, when it must go out, prefers PlayerDB/ashcon and
    **never** touches the aggressively-ratelimited (and host-shared)
    ``api.mojang.com``. Kept here as the injectable default so the domain
    stays pure and tests can stub it. Never raises.
    """
    from app.services.mojang import username_to_uuid

    return await username_to_uuid(ign)


@dataclass(frozen=True)
class IdentityResult:
    mc_uuid: str
    mc_username: str            # authoritative current name (roster/Mojang)
    wynn_username: str          # possibly-stale in-game name (WAPI)
    guild_name: str | None
    guild_tag: str | None
    last_online: datetime       # EPOCH sentinel when API-disabled/unknown
    in_returners_roster: bool


def _parse_last_join(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        return EPOCH
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return EPOCH


async def _fetch_wapi_profile(uuid: str) -> dict | None:
    """WAPI ``/v3/player/{uuid}`` (high priority — interactive). ``None`` on failure."""
    try:
        return await get_wapi().get_json(f"player/{uuid}", priority=PRIO_HIGH)
    except WapiError as exc:
        logger.info("WAPI player lookup failed for %s (%s)", uuid, exc)
        return None
    except Exception:  # noqa: BLE001
        logger.warning("WAPI player lookup errored for %s", uuid, exc_info=True)
        return None


async def resolve_identity(
    ign: str,
    state: AppState,
    *,
    mojang: MojangResolver = mojang_username_to_uuid,
) -> IdentityResult | None:
    """Full resolution: ``ign`` -> UUID + name/guild/last-online profile.

    Returns ``None`` only when the UUID itself cannot be resolved (unknown
    IGN). A reachable-but-WAPI-less result still succeeds with best-effort
    fields so login works offline/in dev.
    """
    uuid = resolve_uuid_cached(ign, state.roster_by_uuid, state.aliases)
    via = "roster/alias cache"
    if uuid is None:
        uuid = await mojang(ign)
        via = "mojang resolver"
    if uuid is None:
        logger.debug("identity: %r unresolved (not in cache, not via mojang)", ign)
        return None
    logger.debug("identity: %r -> %s (%s)", ign, uuid, via)

    profile = await _fetch_wapi_profile(uuid)
    wapi_name = (profile or {}).get("username") if profile else None
    roster_name = state.roster_by_uuid.get(uuid)
    mc_username = roster_name or wapi_name or ign.strip()
    wynn_username = wapi_name or mc_username

    guild = (profile or {}).get("guild") if profile else None
    guild_name = guild.get("name") if isinstance(guild, dict) else None
    guild_tag = guild.get("prefix") if isinstance(guild, dict) else None

    last_online = _parse_last_join((profile or {}).get("lastJoin"))

    return IdentityResult(
        mc_uuid=uuid,
        mc_username=mc_username,
        wynn_username=wynn_username,
        guild_name=guild_name,
        guild_tag=guild_tag,
        last_online=last_online,
        in_returners_roster=uuid in state.roster_by_uuid,
    )
