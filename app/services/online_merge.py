"""online_merge — THE online-truth set (mirror of vetsmod ``/wv list``).

Hard rule (CLAUDE.md): the bare Wynncraft server API is not trusted alone.
We union, exactly like vetsmod ``OnlineMemberService.merge``:

1. ``/v1/outbound/list`` connected clients — **including ``queued`` ones**
   (anni is queue-heavy; a queued player is *connecting*, not offline);
2. WAPI ``/v3/guild/<Returners>`` members flagged ``online`` (our OWN token);
3. a ~grace window of recently-seen uuids so a one-tick blip doesn't flicker
   someone offline.

Names are resolved from the authoritative roster (then the connected payload
as a fallback). This tick also refreshes ``roster``/``aliases`` so identity
resolution (login) stays cheap and offline-rename-safe.
"""

from __future__ import annotations

import logging
import time

from app.services.loop import poll_forever
from app.services.state import AppState, OnlinePlayer
from app.services.tempserver import get_tempserver
from app.services.wapi import PRIO_LOW, WapiError, get_wapi
from app.settings import Settings

logger = logging.getLogger("anni.online")

#: Keep a uuid "online" this long after it last appeared, to mask one-tick
#: upstream blips (mirrors temporary-server's RECENTLY_SEEN_GRACE_SECONDS).
GRACE_SECONDS = 35.0

#: uuid -> (monotonic last-seen, last OnlinePlayer). Module-level so the grace
#: window survives across ticks.
_recent: dict[str, tuple[float, OnlinePlayer]] = {}


def _parse_guild_online(payload: dict) -> dict[str, str]:
    """Return ``{uuid: username}`` for WAPI guild members flagged online.

    v3 ``/guild`` nests members by rank: ``members.<rank>.<username> ->
    {uuid, online, server, ...}`` (plus a numeric ``members.total``). Parse
    defensively — a shape change must degrade, not crash the loop.
    """
    out: dict[str, str] = {}
    members = payload.get("members")
    if not isinstance(members, dict):
        return out
    for rank, group in members.items():
        if rank == "total" or not isinstance(group, dict):
            continue
        for username, info in group.items():
            if isinstance(info, dict) and info.get("online") and info.get("uuid"):
                out[str(info["uuid"])] = username
    return out


async def _tick(state: AppState, settings: Settings) -> None:
    ts = get_tempserver()

    # Roster + aliases first — identity resolution depends on them and they
    # are cheap. Each is independent: a failure of one must not lose the other.
    try:
        roster = await ts.roster()
        if roster:
            state.roster_by_uuid = roster
    except Exception:  # noqa: BLE001
        logger.warning("roster fetch failed — keeping last-good", exc_info=True)
    try:
        aliases = await ts.aliases()
        if aliases:
            state.aliases = aliases
    except Exception:  # noqa: BLE001
        logger.warning("aliases fetch failed — keeping last-good", exc_info=True)
    state.touch("roster_fetched_at")

    merged: dict[str, OnlinePlayer] = {}

    # (1) vetsmod-connected clients — queued players included on purpose.
    for row in await ts.online_list():
        uuid = row.get("uuid")
        if not uuid:
            continue
        name = state.roster_by_uuid.get(uuid) or row.get("username") or uuid[:8]
        merged[uuid] = OnlinePlayer(
            uuid=uuid,
            username=name,
            tier=row.get("tier", "guild"),
            queued=bool(row.get("queued", False)),
        )

    # (2) WAPI guild online (own token, low priority — never block a login).
    try:
        guild = await get_wapi().get_json(
            f"guild/{settings.returners_guild_name}", priority=PRIO_LOW
        )
        for uuid, name in _parse_guild_online(guild).items():
            if uuid not in merged:
                merged[uuid] = OnlinePlayer(
                    uuid=uuid,
                    username=state.roster_by_uuid.get(uuid) or name,
                    tier="guild",
                )
    except WapiError as exc:
        logger.info("guild-online fetch skipped (%s) — list-only this tick", exc)
    except Exception:  # noqa: BLE001
        logger.warning("guild-online fetch failed — list-only this tick", exc_info=True)

    # (3) grace window: re-add anyone seen very recently but missing now.
    now = time.monotonic()
    fresh = len(merged)
    for uuid, player in merged.items():
        _recent[uuid] = (now, player)
    for uuid, (seen_at, player) in list(_recent.items()):
        if now - seen_at > GRACE_SECONDS:
            del _recent[uuid]
        elif uuid not in merged:
            merged[uuid] = player

    state.online_by_uuid = merged
    state.touch("online_fetched_at")
    logger.debug(
        "online tick: %d online (%d this tick + %d in grace), %d queued; "
        "roster=%d aliases=%d",
        len(merged), fresh, len(merged) - fresh,
        sum(1 for p in merged.values() if p.queued),
        len(state.roster_by_uuid), len(state.aliases),
    )


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "online_merge",
        lambda: settings.online_merge_seconds,
        lambda: _tick(state, settings),
    )
