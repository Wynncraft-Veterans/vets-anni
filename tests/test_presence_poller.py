"""presence_poller — the live status sweep over the seeded board.

The pure rule has its own table test (test_presence.py); this drives the
*poller* over real placements + an AppState so the wiring is covered: offline
RSVP buckets, the never-fabricate-online UNKNOWN for a hidden player, and the
two ways a hidden player gets *confirmed* (online-merge, or the api-disabled
probe's inferred-active set) → ONLINE_ELSEWHERE, never a faked world.
"""

from __future__ import annotations

import time

from app.constants import PresenceStatus as S
from app.services import presence_poller
from app.services.state import AppState, OnlinePlayer, _PARTY_LEADER_TTL_SECONDS


def _online(uuid: str, **kw) -> OnlinePlayer:
    return OnlinePlayer(uuid=uuid, username="x", **kw)


async def test_offline_board_maps_by_rsvp_and_hides_api_disabled(seeded):
    p = seeded["players"]
    got = await presence_poller._compute(AppState())  # nobody online

    assert got[p["Wenweia"].mc_uuid] is S.OFFLINE_HARD     # hard RSVP, offline
    assert got[p["Paradrex"].mc_uuid] is S.OFFLINE_SOFT     # soft RSVP, offline
    assert got[p["Faulischlumpf"].mc_uuid] is S.OFFLINE_GONE  # no RSVP, offline
    # API-disabled + unconfirmable -> UNKNOWN even though Metrafish hard-RSVP'd
    # (never faked online, never downgraded to an OFFLINE_* it can't prove).
    assert got[p["Metrafish"].mc_uuid] is S.UNKNOWN


async def test_online_merge_drives_world_states(seeded):
    p = seeded["players"]
    wen = p["Wenweia"].mc_uuid  # party 1, world seeded "AS5"

    # Online but no per-player server signal -> ONLINE_ELSEWHERE (Phase-1/2
    # common case: we can't confirm the world).
    got = await presence_poller._compute(
        AppState(online_by_uuid={wen: _online(wen)})
    )
    assert got[wen] is S.ONLINE_ELSEWHERE

    # Server matches the party world -> ONLINE_WORLD (in-party needs App4).
    got = await presence_poller._compute(
        AppState(online_by_uuid={wen: _online(wen, server="AS5")})
    )
    assert got[wen] is S.ONLINE_WORLD

    # Queued == connecting, never OFFLINE_* (anni is queue-heavy).
    got = await presence_poller._compute(
        AppState(online_by_uuid={wen: _online(wen, queued=True)})
    )
    assert got[wen] is S.ONLINE_ELSEWHERE


async def test_party_leader_corroboration_flips_to_online_party(seeded):
    """S7 corroboration: when the resolved leader of the player's Wynncraft
    party matches their assigned party's host on the board AND the
    observation is fresh, status upgrades from ONLINE_WORLD to ONLINE_PARTY.
    Mirrors what staff sees once a vetsmod client sends a fresh
    ``anni_party_observation`` frame anchored on an organiser."""
    p = seeded["players"]
    wen = p["Wenweia"].mc_uuid       # Party 1 (host: Holidaze, world AS5)
    holidaze = p["Holidaze"].mc_uuid

    # Same setup as the ONLINE_WORLD case (server matches), plus a *fresh*
    # leader corroboration.
    got = await presence_poller._compute(AppState(
        online_by_uuid={wen: _online(wen, server="AS5")},
        party_leader_by_uuid={wen: holidaze},
        party_observation_fetched_at=time.time(),
    ))
    assert got[wen] is S.ONLINE_PARTY


async def test_party_leader_corroboration_stale_falls_back(seeded):
    """If the last observation is older than the TTL we treat the dict as
    stale and degrade to ONLINE_WORLD — a vetsmod disconnect mid-window
    must not pin a user to yellow forever."""
    p = seeded["players"]
    wen = p["Wenweia"].mc_uuid
    holidaze = p["Holidaze"].mc_uuid

    got = await presence_poller._compute(AppState(
        online_by_uuid={wen: _online(wen, server="AS5")},
        party_leader_by_uuid={wen: holidaze},
        party_observation_fetched_at=time.time() - _PARTY_LEADER_TTL_SECONDS - 1,
    ))
    assert got[wen] is S.ONLINE_WORLD


async def test_party_leader_mismatch_stays_online_world(seeded):
    """Defensive: a resolved party whose leader is *not* the assigned host
    must NOT trip ONLINE_PARTY — they're in some other Wynncraft party that
    happens to overlap. Cyan, not yellow."""
    p = seeded["players"]
    wen = p["Wenweia"].mc_uuid
    nazzae = p["Nazzae"].mc_uuid  # different host than Party 1's Holidaze

    got = await presence_poller._compute(AppState(
        online_by_uuid={wen: _online(wen, server="AS5")},
        party_leader_by_uuid={wen: nazzae},
        party_observation_fetched_at=time.time(),
    ))
    assert got[wen] is S.ONLINE_WORLD


async def test_party_corroboration_without_world_match_stays_elsewhere(seeded):
    """A confirmed party leader alone is not enough — they must also be on
    the assigned world. Off-world + corroborated → still ONLINE_ELSEWHERE
    (the dashboard's job is to tell the player to come to the right world,
    not to gloss over them being elsewhere)."""
    p = seeded["players"]
    wen = p["Wenweia"].mc_uuid
    holidaze = p["Holidaze"].mc_uuid

    got = await presence_poller._compute(AppState(
        online_by_uuid={wen: _online(wen, server="NA1")},  # wrong world
        party_leader_by_uuid={wen: holidaze},
        party_observation_fetched_at=time.time(),
    ))
    assert got[wen] is S.ONLINE_ELSEWHERE


async def test_api_disabled_player_confirmed_two_ways(seeded):
    meta = seeded["players"]["Metrafish"].mc_uuid  # api-disabled, Unassigned

    # (1) online-merge actually shows them (vetsmod connection beats WAPI
    #     privacy) -> confirmed online -> ONLINE_ELSEWHERE, not UNKNOWN.
    got = await presence_poller._compute(
        AppState(online_by_uuid={meta: _online(meta)})
    )
    assert got[meta] is S.ONLINE_ELSEWHERE

    # (2) not in online-merge but the slow probe inferred activity.
    got = await presence_poller._compute(AppState(api_active_uuids={meta}))
    assert got[meta] is S.ONLINE_ELSEWHERE


async def test_tick_caches_and_stamps(seeded):
    state = AppState()
    await presence_poller._tick(state, None)  # no WS clients -> no broadcast
    assert state.presence_by_uuid  # populated from the seeded board
    assert state.presence_fetched_at > 0


async def test_no_active_event_is_empty(db):
    assert await presence_poller._compute(AppState()) == {}
