"""party_status_poller — Wynncraft party-membership corroboration.

Closes the App4 corroboration gap on the staff board: a board member who is
online + on the announced world for their party renders **yellow**
(``ONLINE_PARTY``) only when *some* vetsmod-running player who shares that
in-game party has reported the roster via ``party_status``. Without
corroboration the member stays **cyan** (``ONLINE_WORLD``) — we never
fabricate a join.

Source of truth: temp-server ``/v1/outbound/party_status``. It aggregates
``party_status`` control frames from every connected vetsmod client into a
flat ``{member_name_lower: leader_name_lower}`` map. We resolve those names
to mc_uuids on this side (the roster + aliases caches live here too, and
we already pay for them in ``online_merge``) and stash the resolved map on
``state.party_leader_by_uuid``.

The presence classifier reads that map every tick (see
``services/presence_poller.py`` :func:`_compute`) — when
``state.party_leader_by_uuid.get(member_uuid) == party.host.mc_uuid`` for
the player's assigned party, ``in_party_confirmed`` flips to ``True`` and
``ONLINE_WORLD`` upgrades to ``ONLINE_PARTY``.

Resilience matches the other pollers: a bad tick logs, keeps the last-good
map served, and the classifier degrades to ``ONLINE_WORLD`` (never green)
for the affected players.
"""

from __future__ import annotations

import logging

from app.services import hot_window
from app.services.loop import poll_forever
from app.services.state import AppState
from app.services.tempserver import get_tempserver
from app.settings import Settings

logger = logging.getLogger("anni.party_status")


def _resolve(state: AppState, members_by_name: dict[str, str]) -> dict[str, str]:
    """Translate ``{member_name_lower: leader_name_lower}`` → ``{member_uuid:
    leader_uuid}`` using the shared roster + aliases caches.

    Unresolved on either side drops the pairing — better to under-confirm
    (player stays cyan) than to fabricate a join from a partial resolve.
    """
    out: dict[str, str] = {}
    leader_uuid_cache: dict[str, str | None] = {}
    for member_name, leader_name in members_by_name.items():
        if not member_name or not leader_name:
            continue
        member_uuid = state.resolve_uuid(member_name)
        if not member_uuid:
            continue
        if leader_name not in leader_uuid_cache:
            leader_uuid_cache[leader_name] = state.resolve_uuid(leader_name)
        leader_uuid = leader_uuid_cache[leader_name]
        if not leader_uuid:
            continue
        out[member_uuid] = leader_uuid
    return out


async def _tick(state: AppState, settings: Settings) -> None:
    try:
        payload = await get_tempserver().party_status()
    except Exception:  # noqa: BLE001
        logger.warning("party_status fetch failed — keeping last-good",
                       exc_info=True)
        return

    members_by_name = payload.get("members") or {}
    if not isinstance(members_by_name, dict):
        logger.warning("party_status: unexpected payload shape — keeping last-good")
        return

    resolved = _resolve(state, members_by_name)
    state.party_leader_by_uuid = resolved
    state.touch("party_status_fetched_at")
    logger.debug(
        "party_status tick: %d name pairs upstream -> %d resolved uuid pairs",
        len(members_by_name), len(resolved),
    )


async def run(state: AppState, settings: Settings) -> None:
    def _interval() -> float:
        # Ramped during the hot window for the same reason online_merge ramps:
        # party formation churns through events while the anni is forming, and
        # the board should reflect joins/leaves within ~one cycle.
        return float(
            settings.party_status_poll_hot_seconds
            if hot_window.is_currently_hot()
            else settings.party_status_poll_seconds
        )

    await poll_forever("party_status", _interval, lambda: _tick(state, settings))
