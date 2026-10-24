"""S7 ``POST /api/internal/anni-party-observation`` — vetsmod back-report.

Covers the resolution path (roster + alias fallback), the observer-uuid
fallback, the unresolvable-leader short-circuit, secret-gating, the
replace-the-roster semantics (a member a re-observation no longer lists drops
off), the per-member freshness stamps (``state.party_seen_at_by_uuid``), and the
``state.party_leader_by_uuid`` mutation that feeds ``presence_poller``.
"""

from __future__ import annotations

import time

import pytest

from app.constants import PresenceStatus
from app.services import presence_poller
from app.services.state import AppState, OnlinePlayer, _PARTY_LEADER_TTL_SECONDS
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
    assert state.party_seen_at_by_uuid.keys() == {wen.mc_uuid, holidaze.mc_uuid}
    assert all(t > 0 for t in state.party_seen_at_by_uuid.values())


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



async def test_outsiders_resolve_via_the_board(client, seeded):
    """The roster is Returners only. An ally/community member — and a host
    outside the guild — resolve from the active board's own names instead,
    so they can reach ONLINE_PARTY without running vetsmod themselves."""
    p = seeded["players"]
    wen, holidaze = p["Wenweia"], p["Holidaze"]  # party 1 member / host
    state = _state_with_roster(
        {n: pl for n, pl in p.items() if n not in ("Wenweia", "Holidaze")}
    )
    _wire_state(client._transport.app, state)

    res = await client.post(
        "/api/internal/anni-party-observation",
        headers={"X-Introspect-Secret": SECRET},
        json={
            "observer_mc_uuid": p["Nazzae"].mc_uuid,
            # Holidaze hosts party 1 but has no board placement of their own.
            "party_member_usernames": ["wenweia", "Holidaze"],
            "leader_username": "Holidaze",
            "world": "AS5",
        },
    )
    assert res.status_code == 200
    assert res.json()["dropped"] == 0
    assert state.party_leader_by_uuid[wen.mc_uuid] == holidaze.mc_uuid

    # End to end: on party 1's world and confirmed in it -> ONLINE_PARTY.
    state.online_by_uuid = {
        wen.mc_uuid: OnlinePlayer(uuid=wen.mc_uuid, username="Wenweia",
                                  tier="outside", server="AS5"),
    }
    got = await presence_poller._compute(state)
    assert got[wen.mc_uuid] is PresenceStatus.ONLINE_PARTY


async def test_board_resolves_a_stale_wynn_name(client, seeded):
    """``wynn_username`` is keyed too — the name Wynncraft still shows after a
    rename that no alias covers."""
    p = seeded["players"]
    pasta = p["_akaPasta"]
    state = _state_with_roster({n: pl for n, pl in p.items() if n != "_akaPasta"})
    _wire_state(client._transport.app, state)

    res = await client.post(
        "/api/internal/anni-party-observation",
        headers={"X-Introspect-Secret": SECRET},
        json={
            "observer_mc_uuid": p["Holidaze"].mc_uuid,
            "party_member_usernames": [pasta.wynn_username],
            "leader_username": "Holidaze",
            "world": "AS5",
        },
    )
    assert res.status_code == 200
    assert state.party_leader_by_uuid[pasta.mc_uuid] == p["Holidaze"].mc_uuid

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


async def _observe(client, observer, members, leader):
    return await client.post(
        "/api/internal/anni-party-observation",
        headers={"X-Introspect-Secret": SECRET},
        json={
            "observer_mc_uuid": observer,
            "party_member_usernames": members,
            "leader_username": leader,
            "world": "AS5",
        },
    )


async def test_observation_for_same_leader_drops_members_no_longer_listed(
    client, seeded
):
    """A member who left the party stops being corroborated as soon as the
    leader's party is observed again without them."""
    p = seeded["players"]
    wen, pasta, holidaze = p["Wenweia"], p["_akaPasta"], p["Holidaze"]
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)

    first = await _observe(
        client, wen.mc_uuid, ["Holidaze", "Wenweia", "_akaPasta"], "Holidaze"
    )
    assert first.status_code == 200
    assert state.party_leader_by_uuid[pasta.mc_uuid] == holidaze.mc_uuid

    # _akaPasta leaves; the next observation lists only the two who remain.
    second = await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia"], "Holidaze")
    assert second.status_code == 200

    assert pasta.mc_uuid not in state.party_leader_by_uuid
    assert state.party_leader_by_uuid[wen.mc_uuid] == holidaze.mc_uuid
    assert state.party_leader_by_uuid[holidaze.mc_uuid] == holidaze.mc_uuid


async def test_an_unresolved_name_drops_nobody_from_the_leaders_roster(client, seeded):
    """A new member paired only by their own report (their name is not in the
    cache yet) keeps the pairing when another member's report lists that name
    and cannot resolve it: an observation with an unresolved name is not
    complete enough to drop anyone."""
    p = seeded["players"]
    wen, holidaze = p["Wenweia"], p["Holidaze"]
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)
    new_uuid = "00000000-0000-0000-0000-000000000abc"

    own = await _observe(client, new_uuid, ["Holidaze", "NobodyKnowsMe"], "Holidaze")
    assert own.status_code == 200
    assert state.party_leader_by_uuid[new_uuid] == holidaze.mc_uuid

    other = await _observe(
        client, wen.mc_uuid, ["Holidaze", "Wenweia", "NobodyKnowsMe"], "Holidaze"
    )
    assert other.status_code == 200
    assert state.party_leader_by_uuid[new_uuid] == holidaze.mc_uuid
    assert state.party_leader_by_uuid[wen.mc_uuid] == holidaze.mc_uuid


async def test_dropped_member_degrades_to_online_world(client, seeded):
    """End to end: the departed member falls from ONLINE_PARTY to ONLINE_WORLD."""
    p = seeded["players"]
    wen, pasta = p["Wenweia"], p["_akaPasta"]
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)
    state.online_by_uuid = {
        pasta.mc_uuid: OnlinePlayer(uuid=pasta.mc_uuid, username="_akaPasta",
                                    tier="guild", server="AS5"),
    }
    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia", "_akaPasta"], "Holidaze")
    assert (await presence_poller._compute(state))[pasta.mc_uuid] is PresenceStatus.ONLINE_PARTY

    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia"], "Holidaze")

    assert (await presence_poller._compute(state))[pasta.mc_uuid] is PresenceStatus.ONLINE_WORLD


async def test_observation_for_other_leader_keeps_this_leaders_members(client, seeded):
    """Replacing one leader's roster must not touch another party's entries."""
    p = seeded["players"]
    wen, pasta, nazzae, holidaze = (
        p["Wenweia"], p["_akaPasta"], p["Nazzae"], p["Holidaze"],
    )
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)

    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia"], "Holidaze")
    # A different party, led by Nazzae, is observed afterwards.
    res = await _observe(client, pasta.mc_uuid, ["Nazzae", "_akaPasta"], "Nazzae")
    assert res.status_code == 200

    assert state.party_leader_by_uuid[wen.mc_uuid] == holidaze.mc_uuid
    assert state.party_leader_by_uuid[holidaze.mc_uuid] == holidaze.mc_uuid
    assert state.party_leader_by_uuid[pasta.mc_uuid] == nazzae.mc_uuid
    assert state.party_leader_by_uuid[nazzae.mc_uuid] == nazzae.mc_uuid


async def test_member_who_joined_another_party_moves_without_being_dropped(
    client, seeded
):
    """A leaver's own report maps them to the new leader; the old leader's next
    observation then has nothing of theirs left to drop or overwrite."""
    p = seeded["players"]
    wen, pasta, nazzae, holidaze = (
        p["Wenweia"], p["_akaPasta"], p["Nazzae"], p["Holidaze"],
    )
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)

    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia", "_akaPasta"], "Holidaze")
    await _observe(client, pasta.mc_uuid, ["Nazzae", "_akaPasta"], "Nazzae")
    assert state.party_leader_by_uuid[pasta.mc_uuid] == nazzae.mc_uuid

    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia"], "Holidaze")

    assert state.party_leader_by_uuid[pasta.mc_uuid] == nazzae.mc_uuid
    assert state.party_leader_by_uuid[wen.mc_uuid] == holidaze.mc_uuid


async def test_unresolved_leader_still_a_noop_and_drops_nothing(client, seeded):
    p = seeded["players"]
    wen, pasta, holidaze = p["Wenweia"], p["_akaPasta"], p["Holidaze"]
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)
    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia", "_akaPasta"], "Holidaze")
    before = dict(state.party_leader_by_uuid)
    stamps = dict(state.party_seen_at_by_uuid)

    res = await _observe(client, wen.mc_uuid, ["Wenweia"], "RandomStranger")

    assert res.status_code == 200 and res.json()["resolved"] == 0
    assert state.party_leader_by_uuid == before
    assert state.party_seen_at_by_uuid == stamps
    assert state.party_leader_by_uuid[pasta.mc_uuid] == holidaze.mc_uuid


def _age(state: AppState, uuid: str, seconds: float) -> None:
    """Pretend the last observation listing ``uuid`` was ``seconds`` ago."""
    state.party_seen_at_by_uuid[uuid] = time.time() - seconds


async def test_observation_stamps_only_the_members_it_lists(client, seeded):
    p = seeded["players"]
    wen, pasta, holidaze, nazzae = (
        p["Wenweia"], p["_akaPasta"], p["Holidaze"], p["Nazzae"],
    )
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)

    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia", "_akaPasta"], "Holidaze")

    assert state.party_seen_at_by_uuid.keys() == {
        wen.mc_uuid, pasta.mc_uuid, holidaze.mc_uuid,
    }
    assert nazzae.mc_uuid not in state.party_seen_at_by_uuid


async def test_the_observer_fallback_member_is_stamped(client, seeded):
    """The observer maps to the leader even when their name did not resolve,
    and that pairing is stamped like any other."""
    p = seeded["players"]
    holidaze = p["Holidaze"]
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)
    new_uuid = "00000000-0000-0000-0000-000000000abc"

    await _observe(client, new_uuid, ["Holidaze", "NobodyKnowsMe"], "Holidaze")

    assert state.party_seen_at_by_uuid[new_uuid] > 0
    assert state.party_leader_by_uuid[new_uuid] == holidaze.mc_uuid


async def test_other_leaders_observation_does_not_refresh_this_leaders_members(
    client, seeded
):
    """A second party reporting must not keep the first party's members fresh."""
    p = seeded["players"]
    wen, pasta, nazzae, holidaze = (
        p["Wenweia"], p["_akaPasta"], p["Nazzae"], p["Holidaze"],
    )
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)
    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia"], "Holidaze")
    for uuid in (wen.mc_uuid, holidaze.mc_uuid):
        _age(state, uuid, _PARTY_LEADER_TTL_SECONDS + 1)

    await _observe(client, pasta.mc_uuid, ["Nazzae", "_akaPasta"], "Nazzae")

    now = time.time()
    for uuid in (wen.mc_uuid, holidaze.mc_uuid):
        assert now - state.party_seen_at_by_uuid[uuid] > _PARTY_LEADER_TTL_SECONDS
    for uuid in (pasta.mc_uuid, nazzae.mc_uuid):
        assert now - state.party_seen_at_by_uuid[uuid] < 5
    state.online_by_uuid = {
        wen.mc_uuid: OnlinePlayer(uuid=wen.mc_uuid, username="Wenweia",
                                  tier="guild", server="AS5"),
    }
    assert (await presence_poller._compute(state))[wen.mc_uuid] is PresenceStatus.ONLINE_WORLD


async def test_unlisted_member_expires_even_when_the_observation_has_an_unresolved_name(
    client, seeded
):
    """An observation with an unresolved name cannot drop anyone, so a leaver
    stays in the map; their stamp is not refreshed, so they lapse on their own."""
    p = seeded["players"]
    wen, pasta, holidaze = p["Wenweia"], p["_akaPasta"], p["Holidaze"]
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)
    state.online_by_uuid = {
        pasta.mc_uuid: OnlinePlayer(uuid=pasta.mc_uuid, username="_akaPasta",
                                    tier="guild", server="AS5"),
    }
    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia", "_akaPasta"], "Holidaze")
    assert (await presence_poller._compute(state))[pasta.mc_uuid] is PresenceStatus.ONLINE_PARTY
    for uuid in (wen.mc_uuid, pasta.mc_uuid, holidaze.mc_uuid):
        _age(state, uuid, _PARTY_LEADER_TTL_SECONDS - 5)

    # _akaPasta leaves, and a name in the party does not resolve (dropped > 0).
    res = await _observe(
        client, wen.mc_uuid, ["Holidaze", "Wenweia", "NobodyKnowsMe"], "Holidaze"
    )

    assert res.json()["dropped"] == 1
    assert state.party_leader_by_uuid[pasta.mc_uuid] == holidaze.mc_uuid
    assert time.time() - state.party_seen_at_by_uuid[wen.mc_uuid] < 5
    # Still inside the TTL for now ...
    assert (await presence_poller._compute(state))[pasta.mc_uuid] is PresenceStatus.ONLINE_PARTY
    # ... and gone once the TTL passes with no further listing.
    _age(state, pasta.mc_uuid, _PARTY_LEADER_TTL_SECONDS + 1)
    assert (await presence_poller._compute(state))[pasta.mc_uuid] is PresenceStatus.ONLINE_WORLD


async def test_replace_drops_the_stamp_with_the_entry(client, seeded):
    p = seeded["players"]
    wen, pasta, holidaze = p["Wenweia"], p["_akaPasta"], p["Holidaze"]
    state = _state_with_roster(p)
    _wire_state(client._transport.app, state)
    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia", "_akaPasta"], "Holidaze")
    assert pasta.mc_uuid in state.party_seen_at_by_uuid

    await _observe(client, wen.mc_uuid, ["Holidaze", "Wenweia"], "Holidaze")

    assert pasta.mc_uuid not in state.party_leader_by_uuid
    assert pasta.mc_uuid not in state.party_seen_at_by_uuid
    assert state.party_seen_at_by_uuid.keys() == {wen.mc_uuid, holidaze.mc_uuid}
