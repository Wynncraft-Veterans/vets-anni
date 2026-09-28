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

from app.constants import BucketKind
from app.db.models import AnniEvent, AnniPlayer, BoardPlacement
from app.services import online_merge
from app.services.state import AppState
from app.settings import Settings


@pytest.fixture(autouse=True)
def _reset_wapi_cache():
    """Each test starts with an empty WAPI cache + a fresh grace window."""
    online_merge._wapi_guild_cache = None
    online_merge._wapi_guild_fetched_at = 0.0
    online_merge._recent.clear()
    online_merge._wapi_player_cache.clear()
    yield
    online_merge._wapi_guild_cache = None
    online_merge._wapi_guild_fetched_at = 0.0
    online_merge._recent.clear()
    online_merge._wapi_player_cache.clear()


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


async def test_vetsmod_carries_server_through_to_online_player(db, monkeypatch):
    """temp-server enriches ``/v1/outbound/list`` with the player's world via
    its tablist join (Phase 1b). When that field comes through, the vetsmod
    branch must surface it onto ``OnlinePlayer.server`` so the WAPI branch
    doesn't need to backfill (and importantly, doesn't clobber it)."""
    class _ServerAwareTemp:
        async def roster(self) -> dict:
            return {"uuid-ally": "AllyDude"}

        async def aliases(self) -> dict:
            return {}

        async def online_list(self) -> list[dict]:
            return [{
                "uuid": "uuid-ally",
                "username": "AllyDude",
                "tier": "guild",
                "queued": False,
                "server": "EU15",
            }]

    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _ServerAwareTemp())
    monkeypatch.setattr(online_merge, "get_wapi", lambda: _FakeWapi({"members": {}}))

    state = AppState()
    await online_merge._tick(state, Settings(wapi_guild_ttl_seconds=120))

    assert state.online_by_uuid["uuid-ally"].server == "EU15"


async def test_vetsmod_server_survives_wapi_branch_when_wapi_also_reports(db, monkeypatch):
    """When BOTH sources report a server for the same uuid, the vetsmod-fed
    value must win — its tablist is "closer to truth" (the user's own client
    snapshot) than the 120s-cached WAPI guild payload."""
    class _BothReportSrvTemp:
        async def roster(self) -> dict:
            return {"uuid-x": "Bothie"}

        async def aliases(self) -> dict:
            return {}

        async def online_list(self) -> list[dict]:
            return [{
                "uuid": "uuid-x",
                "username": "Bothie",
                "tier": "guild",
                "queued": False,
                "server": "EU15",  # vetsmod path says EU15
            }]

    wapi = _FakeWapi({
        "members": {
            "captain": {
                "Bothie": {
                    "uuid": "uuid-x",
                    "online": True,
                    "server": "NA1",  # WAPI path (stale cache) says NA1
                },
            },
        },
    })
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _BothReportSrvTemp())
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)

    state = AppState()
    await online_merge._tick(state, Settings(wapi_guild_ttl_seconds=120))

    assert state.online_by_uuid["uuid-x"].server == "EU15", \
        "vetsmod-reported server (via temp-server tablist join) must not be clobbered by WAPI"


async def test_wapi_server_backfills_when_vetsmod_branch_inserted_first(db, monkeypatch):
    """A guild member who appears in BOTH the vetsmod ``/v1/outbound/list``
    branch AND the WAPI guild payload must end up with ``server`` populated
    from WAPI — the vetsmod branch runs first and creates the record with
    ``server=None``, and historically the WAPI branch skipped the uuid
    entirely, leaving the presence classifier with no world signal and the
    player misclassified as ``ONLINE_ELSEWHERE``.

    Also assert that ``queued`` (which only vetsmod reports) survives the
    backfill — WAPI never sees queue state, so a `replace(..., server=…)` must
    keep the vetsmod flags intact.
    """
    class _BothSourcesTemp:
        async def roster(self) -> dict:
            return {"uuid-both": "DualSrc"}

        async def aliases(self) -> dict:
            return {}

        async def online_list(self) -> list[dict]:
            return [{
                "uuid": "uuid-both",
                "username": "DualSrc",
                "queued": True,
            }]

    payload = {
        "members": {
            "captain": {
                "DualSrc": {
                    "uuid": "uuid-both",
                    "online": True,
                    "server": "EU15",
                },
            },
        },
    }
    wapi = _FakeWapi(payload)
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _BothSourcesTemp())

    state = AppState()
    await online_merge._tick(state, Settings(wapi_guild_ttl_seconds=120))

    record = state.online_by_uuid["uuid-both"]
    assert record.server == "EU15", "WAPI server must backfill onto the vetsmod-inserted record"
    assert record.queued is True, "queued flag from vetsmod must survive the WAPI backfill"


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


# --- source (3): board members outside the guild payload --------------------

class _RoutedWapi:
    """Answers ``guild/...`` with the guild payload and ``player/<uuid>`` from
    ``players`` (a missing uuid raises like a WAPI 404). Records every path."""

    def __init__(self, guild: dict, players: dict[str, dict]) -> None:
        self.guild = guild
        self.players = players
        self.paths: list[str] = []

    async def get_json(self, path: str, *, priority: int = 0) -> dict:
        self.paths.append(path)
        if path.startswith("guild/"):
            return self.guild
        uuid = path.removeprefix("player/")
        if uuid not in self.players:
            raise online_merge.WapiError(f"WAPI 404 for {path}")
        return self.players[uuid]


class _NobodyTemp:
    async def roster(self) -> dict:
        return {}

    async def aliases(self) -> dict:
        return {}

    async def online_list(self) -> list[dict]:
        return []


async def _board(*uuids: str) -> None:
    event = await AnniEvent.create(stamp_epoch=2_000_000_000, is_active=True)
    for i, uuid in enumerate(uuids):
        player = await AnniPlayer.create(mc_uuid=uuid, mc_username=uuid)
        await BoardPlacement.create(
            event=event, player=player, bucket=BucketKind.UNASSIGNED, sort_index=i,
        )


def _player(*, online: bool, server: str | None = None, hidden: bool = False) -> dict:
    return {
        "username": "Outsider",
        "online": online,
        "server": server,
        "restrictions": {"onlineStatus": hidden},
    }


async def test_outsider_on_the_board_is_looked_up_per_player(db, monkeypatch):
    """Regression: an ally/community member without vetsmod was in neither
    the vetsmod list nor the Returners guild payload, so nothing ever asked
    WAPI about them and the board showed them OFFLINE while they were on."""
    await _board("uuid-ally", "uuid-guildie")
    wapi = _RoutedWapi(
        {"members": {"recruit": {"G": {"uuid": "uuid-guildie", "online": False}}}},
        {"uuid-ally": _player(online=True, server="EU14")},
    )
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _NobodyTemp())

    state = AppState()
    await online_merge._tick(state, Settings())

    assert state.online_by_uuid["uuid-ally"].server == "EU14"
    assert state.api_hidden_by_uuid == {"uuid-ally": False}
    # The guild payload already answers for guild members — no per-player call.
    assert "player/uuid-guildie" not in wapi.paths
    assert "uuid-guildie" not in state.online_by_uuid


async def test_hidden_outsider_is_a_verdict_not_an_offline(db, monkeypatch):
    """A hidden online status is recorded live so presence reads UNKNOWN; the
    ``online: false`` WAPI sends for them must not count as a sighting."""
    await _board("uuid-hidden")
    wapi = _RoutedWapi({"members": {}},
                       {"uuid-hidden": _player(online=False, hidden=True)})
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _NobodyTemp())

    state = AppState()
    await online_merge._tick(state, Settings())

    assert "uuid-hidden" not in state.online_by_uuid
    assert state.api_hidden_by_uuid == {"uuid-hidden": True}


async def test_outsider_fetch_throttled_to_ttl_and_failures_wait_it_out(db, monkeypatch):
    """One call per uuid per TTL — including a 404 (never joined Wynncraft),
    which must not retry every 5s hot tick."""
    await _board("uuid-ally", "uuid-never-joined")
    wapi = _RoutedWapi({"members": {}},
                       {"uuid-ally": _player(online=True, server="EU14")})
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _NobodyTemp())

    state = AppState()
    settings = Settings(wapi_player_ttl_seconds=120)
    await online_merge._tick(state, settings)
    await online_merge._tick(state, settings)

    assert wapi.paths.count("player/uuid-ally") == 1
    assert wapi.paths.count("player/uuid-never-joined") == 1
    # Cached payload re-parsed on the second tick: still online.
    assert state.online_by_uuid["uuid-ally"].server == "EU14"
    # No verdict for a uuid WAPI never answered — the stored flag stands.
    assert "uuid-never-joined" not in state.api_hidden_by_uuid


async def test_outsider_fetches_capped_per_tick(db, monkeypatch):
    await _board(*(f"uuid-{i}" for i in range(7)))
    wapi = _RoutedWapi({"members": {}}, {})
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _NobodyTemp())

    state = AppState()
    settings = Settings(wapi_player_fetch_cap_per_tick=5)
    await online_merge._tick(state, settings)
    assert sum(p.startswith("player/") for p in wapi.paths) == 5
    await online_merge._tick(state, settings)
    # The two left over go next tick; the five just fetched wait out the TTL.
    assert sum(p.startswith("player/") for p in wapi.paths) == 7


async def test_vetsmod_outsider_with_a_world_is_not_fetched(db, monkeypatch):
    """vetsmod already placed them on a world — nothing for WAPI to add."""
    await _board("uuid-ally")

    class _AllyOnVetsmod(_NobodyTemp):
        async def online_list(self) -> list[dict]:
            return [{"uuid": "uuid-ally", "username": "Ally", "server": "EU15"}]

    wapi = _RoutedWapi({"members": {}},
                       {"uuid-ally": _player(online=True, server="NA1")})
    monkeypatch.setattr(online_merge, "get_wapi", lambda: wapi)
    monkeypatch.setattr(online_merge, "get_tempserver", lambda: _AllyOnVetsmod())

    state = AppState()
    await online_merge._tick(state, Settings())

    assert "player/uuid-ally" not in wapi.paths
    assert state.online_by_uuid["uuid-ally"].server == "EU15"
