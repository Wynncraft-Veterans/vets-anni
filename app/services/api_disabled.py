"""api_disabled — slow purgelist-style probe for hidden players.

A player whose ``last_online`` is the epoch sentinel has disabled their
Wynncraft API, so WAPI normally can't say if they're online. The **primary**
inference is the online-merge set (a vetsmod connection shows them regardless
of WAPI privacy) and that is consumed directly by ``presence_poller``. This
task is the slow (~5 min) **secondary**: for board members who are API-disabled
*and* absent from online-merge, probe ``/v3/player`` and look for a
between-tick change in ``server``/``lastJoin`` — a change proves activity
(dazebot's purgelist heuristic, ``cogs/activity`` + ``is_last_online_unknown``).

Confirmable → the uuid joins ``state.api_active_uuids`` and the presence poller
upgrades them to ``ONLINE_ELSEWHERE`` (we still don't know their world).
**Unconfirmable → they are left out and stay ``UNKNOWN``** — the spec's hard
"never fabricate online" rule. A failed probe carries the previous inference
forward (last-good per uuid) so a flaky WAPI doesn't flap everyone to UNKNOWN.
"""

from __future__ import annotations

import logging

from app.db.lifecycle import get_active_event
from app.db.models import BoardPlacement
from app.domain import identity
from app.services.loop import poll_forever
from app.services.state import AppState
from app.services.wapi import PRIO_LOW, WapiError, get_wapi
from app.settings import Settings

logger = logging.getLogger("anni.apidisabled")

#: uuid -> (lastJoin_raw, server) from the previous successful probe. Module
#: level so the between-tick comparison survives across ticks.
_probe: dict[str, tuple[str | None, str | None]] = {}


def _looks_online(profile: dict) -> bool:
    """WAPI explicitly says online (a real signal even with the epoch
    sentinel — the stored ``last_online`` can just be a stale login value)."""
    if profile.get("online"):
        return True
    return bool(profile.get("server"))


async def _tick(state: AppState, settings: Settings) -> None:
    event = await get_active_event()
    if event is None:
        _probe.clear()
        state.api_active_uuids = set()
        state.touch("api_probe_at")
        return

    placements = (
        await BoardPlacement.filter(event=event).select_related("player")
    )
    targets = [
        p.player
        for p in placements
        if identity.is_api_disabled(p.player.last_online)
        and state.is_online(p.player.mc_uuid) is None
    ]

    active: set[str] = set()
    wapi = get_wapi()
    for player in targets:
        uuid = player.mc_uuid
        try:
            profile = await wapi.get_json(f"player/{uuid}", priority=PRIO_LOW)
        except WapiError as exc:
            logger.info("api-disabled probe skipped for %s (%s)", uuid, exc)
            if uuid in state.api_active_uuids:
                active.add(uuid)  # last-good: keep prior inference
            continue
        except Exception:  # noqa: BLE001 - resilience is the whole point
            logger.warning("api-disabled probe errored for %s", uuid,
                            exc_info=True)
            if uuid in state.api_active_uuids:
                active.add(uuid)
            continue

        profile = profile if isinstance(profile, dict) else {}
        last_join = profile.get("lastJoin")
        server = profile.get("server")
        prev = _probe.get(uuid)
        moved = prev is not None and (
            prev[0] != last_join or prev[1] != server
        )
        if _looks_online(profile) or moved:
            active.add(uuid)
            logger.debug("api-disabled %s inferred active (%s)", uuid,
                         "online" if _looks_online(profile) else "moved")
        _probe[uuid] = (last_join, server)

    # Forget probe state for anyone no longer a target (left the board / wiped).
    live = {p.mc_uuid for p in targets}
    for stale in [u for u in _probe if u not in live]:
        del _probe[stale]

    state.api_active_uuids = active
    state.touch("api_probe_at")
    logger.debug(
        "api-disabled tick: %d hidden board members, %d inferred active",
        len(targets), len(active),
    )


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "api_disabled",
        lambda: settings.api_disabled_probe_seconds,
        lambda: _tick(state, settings),
    )
