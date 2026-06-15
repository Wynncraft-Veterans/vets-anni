"""party_status_poller — Phase 2 corroboration source.

Validates: (1) the name → uuid resolution path uses the same roster + aliases
caches `online_merge` populates, (2) unresolvable names degrade silently
(unbroken pair → no entry), (3) a fetched payload lands on
``state.party_leader_by_uuid`` and stamps the fetched_at.
"""

from __future__ import annotations

import pytest

from app.services import party_status_poller
from app.services.state import AppState
from app.settings import Settings


class _FakeTempserver:
    """Returns canned ``/v1/outbound/party_status`` payloads. Only the one
    method the poller calls is implemented."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def party_status(self) -> dict:
        self.calls += 1
        return self.payload


@pytest.fixture
def _state_with_roster() -> AppState:
    """An AppState pre-seeded with a roster that resolves three names."""
    state = AppState()
    state.roster_by_uuid = {
        "uuid-leader": "Holidaze",
        "uuid-member": "Wenweia",
        "uuid-other":  "OtherDude",
    }
    return state


async def test_resolves_name_pairs_via_roster(_state_with_roster, monkeypatch):
    state = _state_with_roster
    payload = {
        "members": {
            "wenweia": "holidaze",   # member -> leader (both resolvable)
            "otherdude": "holidaze",
        },
        "updated_at": 1234567890.0,
    }
    monkeypatch.setattr(
        party_status_poller, "get_tempserver",
        lambda: _FakeTempserver(payload),
    )

    await party_status_poller._tick(state, Settings())

    assert state.party_leader_by_uuid == {
        "uuid-member": "uuid-leader",
        "uuid-other":  "uuid-leader",
    }
    assert state.party_status_fetched_at > 0


async def test_unresolvable_names_are_dropped(_state_with_roster, monkeypatch):
    """An unknown name on either side of a pair must drop the pair entirely —
    we never fabricate a uuid, and we'd rather under-confirm than guess."""
    state = _state_with_roster
    payload = {
        "members": {
            "wenweia": "ghostleader",        # leader unresolvable
            "ghostmember": "holidaze",        # member unresolvable
            "otherdude": "holidaze",          # both resolvable
        },
    }
    monkeypatch.setattr(
        party_status_poller, "get_tempserver",
        lambda: _FakeTempserver(payload),
    )

    await party_status_poller._tick(state, Settings())

    # Only the fully-resolvable pair survives.
    assert state.party_leader_by_uuid == {"uuid-other": "uuid-leader"}


async def test_aliases_cover_renamed_players(monkeypatch):
    """A player whose Wynncraft tab name lags behind a Mojang rename should
    still resolve via ``state.aliases`` (the legacy-name → uuid map). Mirror
    of the resolution path online_merge already relies on."""
    state = AppState()
    state.roster_by_uuid = {"uuid-leader": "Holidaze"}
    state.aliases = {"oldname": "uuid-member"}   # legacy name -> uuid

    payload = {"members": {"oldname": "holidaze"}}
    monkeypatch.setattr(
        party_status_poller, "get_tempserver",
        lambda: _FakeTempserver(payload),
    )

    await party_status_poller._tick(state, Settings())

    assert state.party_leader_by_uuid == {"uuid-member": "uuid-leader"}


async def test_empty_leader_drops_pairs(_state_with_roster, monkeypatch):
    """A pair whose leader-name side is empty (server-side never maps a
    leaderless player, but defence in depth) silently drops."""
    state = _state_with_roster
    payload = {"members": {"wenweia": ""}}
    monkeypatch.setattr(
        party_status_poller, "get_tempserver",
        lambda: _FakeTempserver(payload),
    )

    await party_status_poller._tick(state, Settings())

    assert state.party_leader_by_uuid == {}


async def test_fetch_failure_keeps_last_good(_state_with_roster, monkeypatch):
    """A failed fetch must not wipe the previous tick's resolved map — the
    presence classifier keeps reading last-good while the upstream recovers."""
    state = _state_with_roster
    state.party_leader_by_uuid = {"uuid-member": "uuid-leader"}  # last good

    class _BoomTempserver:
        async def party_status(self) -> dict:
            raise RuntimeError("simulated outage")

    monkeypatch.setattr(
        party_status_poller, "get_tempserver", lambda: _BoomTempserver(),
    )

    await party_status_poller._tick(state, Settings())

    # Last-good preserved, NOT wiped.
    assert state.party_leader_by_uuid == {"uuid-member": "uuid-leader"}
