"""presence_poller — live "how we see each board member right now".

Every ~10 s it recomputes :class:`PresenceStatus` for every member of the
active event's board (the pure rule is ``domain/presence`` — the same one the
user dashboard's Specific module renders), then **diffs against last tick** and
pushes only the changes to the board hub as a ``PATCH`` of status-border
updates. The full map is also cached on :class:`AppState` so a fresh SSR paint
or a non-WS client shows the identical status without recomputing.

Online truth is the online-merge set (hard rule — never the bare server API),
plus one extra signal: an API-disabled player the slow ``api_disabled`` probe
*inferred* active counts as online (→ ``ONLINE_ELSEWHERE``, since we still
don't know their world) rather than ``UNKNOWN``. We never invent a more
specific online status for a hidden player — the spec's "never fabricate
online".

Resilience copied from the other pollers: a bad tick logs and is swallowed,
last-good ``presence_by_uuid`` stays served.
"""

from __future__ import annotations

import logging
import time

from app.constants import AttendanceNotice, PresenceStatus
from app.db.lifecycle import get_active_event
from app.db.models import BoardPlacement, Rsvp
from app.domain import identity, presence
from app.domain.colourblind import status_chip
from app.services import hot_window
from app.services.loop import poll_forever
from app.services.state import AppState, _PARTY_LEADER_TTL_SECONDS
from app.settings import Settings

logger = logging.getLogger("anni.presence")


async def _compute(state: AppState) -> dict[str, PresenceStatus]:
    """Status for every board member of the active event (``{}`` if no anni)."""
    event = await get_active_event()
    if event is None:
        return {}

    now = int(time.time())
    seconds = event.stamp_epoch - now if event.stamp_epoch > now else None

    # One query each for placements + non-revoked RSVPs, then a pure pass.
    placements = (
        await BoardPlacement.filter(event=event)
        .select_related("player", "party")
    )
    rsvps = await Rsvp.filter(event=event, revoked_at=None).select_related("player")
    notice_by_uuid: dict[str, AttendanceNotice] = {
        r.player.mc_uuid: r.notice for r in rsvps
    }

    out: dict[str, PresenceStatus] = {}
    for p in placements:
        uuid = p.player.mc_uuid
        online = state.is_online(uuid)
        api_disabled = identity.is_api_disabled(p.player.last_online)
        # online-merge is authoritative; the probe-inferred set only ever
        # *adds* an api-disabled player (never removes / never fabricates a
        # specific world), so they surface as ONLINE_ELSEWHERE not UNKNOWN.
        is_online = online is not None or (
            api_disabled and uuid in state.api_active_uuids
        )
        party = p.party
        # Corroboration: vetsmod-reporting players send their Wynncraft party
        # roster via the S7 ``anni_party_observation`` frame when an organiser
        # is in their party; the endpoint resolves names → uuids and writes
        # ``state.party_leader_by_uuid`` (member_uuid -> leader_uuid). We only
        # count as "confirmed in party" when (a) the dict is fresh (a vetsmod
        # disconnect mid-window must not pin a user to ONLINE_PARTY forever)
        # and (b) the resolved leader matches the host the staff board
        # assigned. Anything weaker degrades to ONLINE_WORLD — we never
        # fabricate a join.
        # ``Party.host`` is a FK to ``AnniPlayer.mc_uuid`` (the AnniPlayer PK),
        # so ``host_id`` IS the host's mc_uuid — no extra fetch needed.
        host_uuid = party.host_id if party else None
        party_fresh = (
            time.time() - state.party_status_fetched_at
            < _PARTY_LEADER_TTL_SECONDS
        )
        in_party_confirmed = bool(
            host_uuid
            and party_fresh
            and state.party_leader_by_uuid.get(uuid) == host_uuid
        )
        out[uuid] = presence.classify(
            presence.PresenceInputs(
                online=is_online,
                queued=bool(online and online.queued),
                api_disabled=api_disabled,
                rsvp_notice=notice_by_uuid.get(uuid),
                has_party=party is not None,
                party_world=party.world if party else None,
                party_created=party is not None,
                current_server=online.server if online else None,
                in_party_confirmed=in_party_confirmed,
                seconds_to_anni=seconds,
            )
        )
    return out


async def _tick(state: AppState, settings: Settings) -> None:
    new = await _compute(state)
    old = state.presence_by_uuid
    new_values = {u: s.value for u, s in new.items()}

    changed = [u for u, v in new_values.items() if old.get(u) != v]
    dropped = [u for u in old if u not in new_values]  # left the board / wiped

    state.presence_by_uuid = new_values
    state.touch("presence_fetched_at")

    if not changed and not dropped:
        logger.debug("presence tick: %d members, no change", len(new))
        return

    # Lazy import keeps the services layer free of any web import at module
    # load (the hub itself is FastAPI-free; this is the planned poller->hub
    # broadcast path).
    from app.web.ws.board_hub import get_board_hub

    hub = get_board_hub()
    ops = [
        {
            "op": "presence",
            "player_uuid": u,
            "status": new_values[u],
            "chip": status_chip(new[u]),
        }
        for u in changed
    ]
    logger.debug(
        "presence tick: %d members, %d changed, %d dropped -> %d ws clients",
        len(new), len(changed), len(dropped), hub.client_count,
    )
    if ops:
        await hub.broadcast_patch(ops)


async def run(state: AppState, settings: Settings) -> None:
    def _interval() -> float:
        # Pure-in-memory recompute over ``state.online_by_uuid``; cheap. We
        # ramp inside the hot window so the WS clients see status changes
        # within ~one online_merge tick (also ramped). Outside the window
        # the normal 10s cadence is plenty.
        return float(
            settings.presence_poll_hot_seconds
            if hot_window.is_currently_hot()
            else settings.presence_poll_seconds
        )

    await poll_forever("presence", _interval, lambda: _tick(state, settings))
