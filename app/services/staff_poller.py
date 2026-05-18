"""staff_poller — the online-staff snapshot.

``/v1/outbound/staff`` is online-only and already WAPI-paid by
temporary-server, so this is a cheap mirror. Phase 1 uses it for the overview
("staff online" count) and Phase 2's organiser pickers; it never spends our
token.
"""

from __future__ import annotations

import logging

from app.services.loop import poll_forever
from app.services.state import AppState
from app.services.tempserver import get_tempserver
from app.settings import Settings

logger = logging.getLogger("anni.staff")


async def _tick(state: AppState, settings: Settings) -> None:
    rows = await get_tempserver().staff()
    snapshot: dict[str, dict] = {}
    for row in rows:
        uuid = row.get("uuid")
        if not uuid:
            continue
        snapshot[uuid] = {
            "uuid": uuid,
            "username": row.get("username") or uuid[:8],
            "rank": row.get("rank") or "captain",
            "online": bool(row.get("online", True)),
            "server": row.get("server"),
        }
    # Replace wholesale: the endpoint is authoritative for "who is online now".
    state.online_staff = snapshot
    state.touch("staff_fetched_at")
    logger.debug(
        "staff tick: %d online%s",
        len(snapshot),
        " (" + ", ".join(sorted(v["username"] for v in snapshot.values())) + ")"
        if snapshot else "",
    )


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "staff",
        lambda: settings.staff_poll_seconds,
        lambda: _tick(state, settings),
    )
