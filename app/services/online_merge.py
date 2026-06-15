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
from dataclasses import replace

from app.services import hot_window
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

#: Last successful WAPI guild fetch — payload + monotonic timestamp. We poll
#: temp-server every tick (~5s in the hot window) but only **re-fetch** the
#: WAPI guild endpoint once its 120s ``Cache-Control: max-age`` has elapsed.
#: Polite to upstream + indistinguishable from polling at TTL since cloudflare
#: would serve the same body anyway. Cached payload is re-parsed every tick
#: so the merged dict still includes WAPI-only online members (they don't
#: vanish from ``state.online_by_uuid`` just because we skipped the fetch).
_wapi_guild_cache: dict | None = None
_wapi_guild_fetched_at: float = 0.0


def _parse_guild_online(payload: dict) -> dict[str, tuple[str, str | None]]:
    """Return ``{uuid: (username, server)}`` for WAPI guild members flagged
    online. ``server`` is the current world string (e.g. ``"WC1"``) or
    ``None`` when the payload omits it — keeping the field lets the
    presence classifier reach ``ONLINE_WORLD``/``ONLINE_PARTY`` instead of
    falling through to ``ELSEWHERE`` for lack of a server signal.

    v3 ``/guild`` nests members by rank: ``members.<rank>.<username> ->
    {uuid, online, server, ...}`` (plus a numeric ``members.total``). Parse
    defensively — a shape change must degrade, not crash the loop.
    """
    out: dict[str, tuple[str, str | None]] = {}
    members = payload.get("members")
    if not isinstance(members, dict):
        return out
    for rank, group in members.items():
        if rank == "total" or not isinstance(group, dict):
            continue
        for username, info in group.items():
            if isinstance(info, dict) and info.get("online") and info.get("uuid"):
                server = info.get("server")
                out[str(info["uuid"])] = (
                    username,
                    str(server) if isinstance(server, str) and server else None,
                )
    return out


def _parse_guild_staff(payload: dict, staff_ranks: frozenset[str]) -> dict[str, dict]:
    """``{uuid: {"uuid","username","rank"}}`` for EVERY guild member (online
    or not) whose rank is in ``staff_ranks`` — the lead-organiser candidate
    set. Same defensive nested-by-rank parse as :func:`_parse_guild_online`;
    rank is matched case-insensitively (WAPI keys are lower-case)."""
    out: dict[str, dict] = {}
    members = payload.get("members")
    if not isinstance(members, dict):
        return out
    for rank, group in members.items():
        if rank == "total" or not isinstance(group, dict):
            continue
        if rank.lower() not in staff_ranks:
            continue
        for username, info in group.items():
            if isinstance(info, dict) and info.get("uuid"):
                uuid = str(info["uuid"])
                out[uuid] = {"uuid": uuid, "username": username, "rank": rank}
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
    # ``server`` is best-effort: temp-server enriches /v1/outbound/list with
    # the player's world by joining its latest tablist snapshot against the
    # connected_users dict by username. A null/missing/empty value just means
    # no fresh tablist covers that user — the WAPI branch below backfills it
    # for guild members.
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
            server=row.get("server") or None,
        )

    # (2) WAPI guild online — but only re-fetch once per ``wapi_guild_ttl_seconds``
    # (default 120s, matching the endpoint's ``Cache-Control: max-age``). Inside
    # the hot window we tick every 5s; hammering the guild endpoint at that
    # cadence would just yield the same cached body for ~24 ticks out of 25.
    # We cache the payload locally and re-parse it every tick so WAPI-only
    # online members stay in the merged dict between fetches.
    global _wapi_guild_cache, _wapi_guild_fetched_at
    now_mono = time.monotonic()
    if now_mono - _wapi_guild_fetched_at >= settings.wapi_guild_ttl_seconds:
        try:
            _wapi_guild_cache = await get_wapi().get_json(
                f"guild/{settings.returners_guild_name}", priority=PRIO_LOW
            )
            _wapi_guild_fetched_at = now_mono
        except WapiError as exc:
            logger.info("guild-online fetch skipped (%s) — using cached payload", exc)
        except Exception:  # noqa: BLE001
            logger.warning("guild-online fetch failed — using cached payload",
                           exc_info=True)
    if _wapi_guild_cache is not None:
        for uuid, (name, server) in _parse_guild_online(_wapi_guild_cache).items():
            existing = merged.get(uuid)
            if existing is None:
                merged[uuid] = OnlinePlayer(
                    uuid=uuid,
                    username=state.roster_by_uuid.get(uuid) or name,
                    tier="guild",
                    server=server,
                )
            elif server and existing.server is None:
                # vetsmod's /list inserted this uuid first with no server (the
                # /list payload doesn't carry world); without backfilling, the
                # presence classifier sees current_server=None and every
                # vetsmod-connected guild member is misclassified ONLINE_ELSEWHERE.
                merged[uuid] = replace(existing, server=server)
        # Same payload also yields the FULL staff list (offline included) —
        # the lead-organiser candidates. Refresh each tick from the cached
        # payload (cheap pure parse) so a freshly cached fetch propagates
        # without waiting a second tick.
        staff = _parse_guild_staff(_wapi_guild_cache, settings.staff_guild_rank_set)
        if staff:
            state.guild_staff = staff
            state.touch("guild_staff_fetched_at")

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
    def _interval() -> float:
        # Ramped during the T-70 → grace-end window so the dashboard's
        # online list stays at least as fresh as vetsmod ``/wv list``. The
        # hot-window flag is updated by the auto-promoter's tick (worst-
        # case lag bounded by its cadence).
        return float(
            settings.online_merge_hot_seconds
            if hot_window.is_currently_hot()
            else settings.online_merge_seconds
        )

    await poll_forever("online_merge", _interval, lambda: _tick(state, settings))
