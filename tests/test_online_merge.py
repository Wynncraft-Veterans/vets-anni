"""online_merge — the WAPI guild fetch is throttled to its 120s TTL.

The temp-server side of ``_tick`` runs every iteration (real-time push); the
WAPI guild fetch is rate-limited to ``settings.wapi_guild_ttl_seconds`` so
that during the hot-window 5s ramp we don't hammer cloudflare for a body it
would serve from the same cache anyway.

The cached payload is **re-parsed every tick** so WAPI-only online members
stay in ``state.online_by_uuid`` between fetches (no flicker / disappear).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import online_merge
from app.services.state import AppState
from app.settings import Settings


@pytest.fixture(autouse=True)
def _reset_wapi_cache():
    """Each test starts with an empty WAPI cache + a fresh grace window."""
    online_merge._wapi_guild_cache = None
    online_merge._wapi_guild_fetched_at = 0.0
    online_merge._recent.clear()
    yield
    online_merge._wapi_guild_cache = None
    online_merge._wapi_guild_fetched_at = 0.0
    online_merge._recent.clear()


class _FakeWapi:
    """Counts ``get_json`` calls so we can assert the throttle. Returns a
    fixed guild payload with one online member."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def get_json(self, path: str, *, priority: int = 0) -> dict:
        self.calls += 1
        return self.payload


class _FakeTempserver:
    async def roster(self) -> dict:
        return {"uuid-temp": "TempUser"}

    async def aliases(self) -> dict:
        return {}

    async def online_list(self) -> list[dict]:
        return [{"uuid": "uuid-temp", "username": "TempUser", "queued": False}]


_GUILD_PAYLOAD = {
    "members": {
        "captain": {
            "OnlineGuildie": {"uuid": "uuid-guildie", "online": True},
        },
    },
}


async def test_wapi_throttled_within_ttl(db, monkeypatch):
    """Two ticks within the TTL window → exactly one WAPI fetch."""
    wapi = _FakeWapi(_GUILD_PAYLOAD)
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _FakeTempserver())

    state = AppState()
    settings = Settings(wapi_guild_ttl_seconds=120)

    await online_merge._tick(state, settings)
    await online_merge._tick(state, settings)

    assert wapi.calls == 1
    # Both temp-server and WAPI-only members are in the merged dict each tick.
    assert "uuid-temp" in state.online_by_uuid
    assert "uuid-guildie" in state.online_by_uuid


async def test_wapi_refetched_after_ttl(db, monkeypatch):
    """When the cached fetch is older than the TTL, a re-fetch fires."""
    wapi = _FakeWapi(_GUILD_PAYLOAD)
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _FakeTempserver())

    state = AppState()
    settings = Settings(wapi_guild_ttl_seconds=120)

    await online_merge._tick(state, settings)
    # Backdate the cache stamp past the TTL window.
    online_merge._wapi_guild_fetched_at -= 121
    await online_merge._tick(state, settings)

    assert wapi.calls == 2


async def test_wapi_only_online_member_persists_between_fetches(db, monkeypatch):
    """An online guild member surfaced only by WAPI MUST stay in
    ``state.online_by_uuid`` on subsequent ticks within the TTL — we re-parse
    the cached payload, we don't drop them."""
    wapi = _FakeWapi(_GUILD_PAYLOAD)
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _FakeTempserver())

    settings = Settings(wapi_guild_ttl_seconds=120)
    state = AppState()

    # Tick 1: fetch hits WAPI, populates state.
    await online_merge._tick(state, settings)
    assert "uuid-guildie" in state.online_by_uuid

    # Tick 2: WAPI NOT hit (within TTL), but uuid-guildie still present.
    await online_merge._tick(state, settings)
    assert wapi.calls == 1
    assert "uuid-guildie" in state.online_by_uuid


async def test_server_field_from_wapi_payload_reaches_online_player(db, monkeypatch):
    """The WAPI guild payload exposes a per-member ``server`` field; we
    must surface it onto ``OnlinePlayer.server`` so the presence classifier
    can reach ``ONLINE_WORLD``/``ONLINE_PARTY``. Regression: prior to the
    fix this was discarded and the on-world state was unreachable."""
    payload = {
        "members": {
            "captain": {
                "OnWorldGuildie": {
                    "uuid": "uuid-onworld",
                    "online": True,
                    "server": "WC1",
                },
                "OnlineNoServer": {
                    "uuid": "uuid-noserver",
                    "online": True,
                },
            },
        },
    }
    wapi = _FakeWapi(payload)
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _FakeTempserver())

    state = AppState()
    await online_merge._tick(state, Settings(wapi_guild_ttl_seconds=120))

    assert state.online_by_uuid["uuid-onworld"].server == "WC1"
    # Server is optional — missing field stays None, not a crash.
    assert state.online_by_uuid["uuid-noserver"].server is None


async def test_wapi_failure_uses_cached_payload(db, monkeypatch):
    """A WAPI failure on a refetch attempt must not wipe the cache — the
    previous payload keeps serving (last-good resilience)."""
    wapi = _FakeWapi(_GUILD_PAYLOAD)
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _FakeTempserver())

    settings = Settings(wapi_guild_ttl_seconds=120)
    state = AppState()

    await online_merge._tick(state, settings)  # first fetch succeeds
    online_merge._wapi_guild_fetched_at -= 121  # force retry next tick

    # Now make the WAPI call raise.
    async def _boom(_path, priority=0):
        raise online_merge.WapiError("simulated outage")

    wapi.get_json = _boom  # type: ignore[assignment]

    await online_merge._tick(state, settings)

    # WAPI-only member is still present from the cached payload.
    assert "uuid-guildie" in state.online_by_uuid
