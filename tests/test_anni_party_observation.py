"""S7 ``POST /api/internal/anni-party-observation`` — vetsmod back-report.

Covers the resolution path (roster + alias fallback), the observer-uuid
fallback, the unresolvable-leader short-circuit, secret-gating, and the
``state.party_leader_by_uuid`` mutation that feeds ``presence_poller``.
"""

from __future__ import annotations

import pytest

from app.services.state import AppState
from app.settings import get_settings


SECRET = "test-anni-internal"


@pytest.fixture(autouse=True)
def _wire_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "anni_introspect_secret", SECRET)


def _state_with_roster(players: dict[str, object]) -> AppState:
    state = AppState()
    state.roster_by_uuid = {p.mc_uuid: p.mc_username for p in players.values()}
    return state


def _wire_state(client_app, state: AppState) -> None:
    """Replace the ASGI app's shared :class:`AppState` for the duration of a
    test. The route resolves it via ``request.app.state.appstate`` so a
    direct assign is enough."""
    client_app.state.appstate = state


async def test_resolves_member_names_via_roster(client, seeded):
    state = _state_with_roster(seeded["players"])
    _wire_state(client._transport.app, state)

    holidaze = seeded["players"]["Holidaze"]
    wen = seeded["players"]["Wenweia"]

    res = await client.post(
        "/api/internal/anni-party-observation",
        headers={"X-Introspect-Secret": SECRET},
        json={
            "observer_mc_uuid": wen.mc_uuid,
            "party_member_usernames": ["Holidaze", "Wenweia"],
            "leader_username": "Holidaze",
            "world": "AS5",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["resolved"] == 2
    assert body["dropped"] == 0
    assert state.party_leader_by_uuid[wen.mc_uuid] == holidaze.mc_uuid
    assert state.party_leader_by_uuid[holidaze.mc_uuid] == holidaze.mc_uuid
    assert state.party_observation_fetched_at > 0


async def test_resolves_via_alias_fallback(client, seeded):
    """``ISnortPasta`` is _akaPasta's wynn rename; the alias dict resolves it."""
    state = _state_with_roster(seeded["players"])
    pasta = seeded["players"]["_akaPasta"]
    state.aliases = {"isnortpasta": pasta.mc_uuid}
    _wire_state(client._transport.app, state)

    holidaze = seeded["players"]["Holidaze"]

    res = await client.post(
        "/api/internal/anni-party-observation",
        headers={"X-Introspect-Secret": SECRET},
        json={
            "observer_mc_uuid": holidaze.mc_uuid,
            "party_member_usernames": ["ISnortPasta"],
            "leader_username": "Holidaze",
            "world": "AS5",
        },
    )
    assert res.status_code == 200
    assert state.party_leader_by_uuid[pasta.mc_uuid] == holidaze.mc_uuid


async def test_unresolvable_leader_short_circuits(client, seeded):
    state = _state_with_roster(seeded["players"])
    _wire_state(client._transport.app, state)

    wen = seeded["players"]["Wenweia"]
    res = await client.post(
        "/api/internal/anni-party-observation",
        headers={"X-Introspect-Secret": SECRET},
        json={
            "observer_mc_uuid": wen.mc_uuid,
            "party_member_usernames": ["Wenweia"],
            "leader_username": "RandomStranger",
            "world": "AS5",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["resolved"] == 0
    # observer was deduped from the 1 input → 1 candidate, never resolved.
    assert body["dropped"] == 1
    # Nothing should have been written for the observer either.
    assert wen.mc_uuid not in state.party_leader_by_uuid


async def test_observer_fallback_when_username_drops(client, seeded):
    """A brand-new player's username may not be in the roster cache yet; the
    observer's session UUID is still the authoritative entry."""
    state = _state_with_roster(seeded["players"])
    _wire_state(client._transport.app, state)

    holidaze = seeded["players"]["Holidaze"]
    new_uuid = "00000000-0000-0000-0000-000000000abc"

    res = await client.post(
        "/api/internal/anni-party-observation",
        headers={"X-Introspect-Secret": SECRET},
        json={
            "observer_mc_uuid": new_uuid,
            "party_member_usernames": ["NobodyKnowsMe"],
            "leader_username": "Holidaze",
            "world": "AS5",
        },
    )
    assert res.status_code == 200
    # Member name dropped (no roster hit), observer-uuid backfill kicks in.
    assert state.party_leader_by_uuid[new_uuid] == holidaze.mc_uuid


async def test_secret_required(client, seeded):
    res = await client.post(
        "/api/internal/anni-party-observation",
        json={
            "observer_mc_uuid": seeded["players"]["Wenweia"].mc_uuid,
            "party_member_usernames": ["Holidaze"],
            "leader_username": "Holidaze",
            "world": "AS5",
        },
    )
    assert res.status_code == 401


async def test_bad_payload_shapes_rejected(client, seeded):
    headers = {"X-Introspect-Secret": SECRET}
    # missing observer
    r = await client.post(
        "/api/internal/anni-party-observation",
        headers=headers,
        json={"party_member_usernames": [], "leader_username": "x", "world": "AS5"},
    )
    assert r.status_code == 400
    # wrong member type
    r = await client.post(
        "/api/internal/anni-party-observation",
        headers=headers,
        json={
            "observer_mc_uuid": "u",
            "party_member_usernames": [1, 2],
            "leader_username": "x",
            "world": "AS5",
        },
    )
    assert r.status_code == 400
