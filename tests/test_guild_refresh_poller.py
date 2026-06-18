"""guild_refresh_poller — keep ``anni_player.guild`` / ``membership_tier`` fresh.

Track A is the cheap reconciliation against the cached Returners roster +
vetsmod live-tier overlay (zero I/O); Track B is the rate-capped reclassification
that asks dazebot ``check-snapshot`` (authoritative for HONOURARY/WAITLIST) and
optionally one WAPI ``/v3/player/{uuid}`` for non-Returners guild_name freshness.

These tests pin behaviour by simulating an ``AppState`` cache and patching the
sibling-service clients — no real network, no fishbot, no lifespan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import MembershipTier
from app.db.models import AnniPlayer
from app.services import dazebot_client as dazebot_module
from app.services import guild_refresh_poller, wapi as wapi_module
from app.services.dazebot_client import CheckSnapshot
from app.services.state import AppState, OnlinePlayer
from app.settings import Settings


@dataclass
class _Snap:
    """Mirror of CheckSnapshot for fixture-side construction (kwargs only)."""

    in_returners_guild: bool = False
    blocklisted: bool = False
    discord_linked: bool = False
    discord_tier: str | None = None
    discord_hiatus: bool = False
    target_uuid: str = ""
    target_username: str = ""


class _FakeDazebot:
    """Records check_snapshot calls; returns whatever the test queues by uuid.

    Returning ``None`` simulates a transport / unset-secret failure — the
    poller treats it as "skip this row, try again next floor sweep".
    """

    def __init__(self, by_uuid: dict[str, _Snap | None]) -> None:
        self.by_uuid = by_uuid
        self.calls: list[str] = []

    async def check_snapshot(self, mc_uuid: str) -> CheckSnapshot | None:
        self.calls.append(mc_uuid)
        s = self.by_uuid.get(mc_uuid)
        if s is None:
            return None
        return CheckSnapshot(
            target_uuid=s.target_uuid or mc_uuid,
            target_username=s.target_username,
            in_returners_guild=s.in_returners_guild,
            blocklisted=s.blocklisted,
            discord_linked=s.discord_linked,
            discord_tier=s.discord_tier,
            discord_hiatus=s.discord_hiatus,
        )


class _FakeWapi:
    """Returns canned ``/v3/player/{uuid}`` payloads keyed by uuid."""

    def __init__(self, by_uuid: dict[str, dict]) -> None:
        self.by_uuid = by_uuid
        self.calls: list[str] = []

    async def get_json(self, path: str, *, priority: int = 0):  # noqa: ARG002
        # path is "player/<uuid>"
        uuid = path.split("/", 1)[1]
        self.calls.append(uuid)
        if uuid not in self.by_uuid:
            raise wapi_module.WapiError(f"WAPI 404 for {path}")
        return self.by_uuid[uuid]


def _patch_clients(
    monkeypatch,
    daze: _FakeDazebot | None = None,
    wapi: _FakeWapi | None = None,
) -> None:
    if daze is not None:
        monkeypatch.setattr(
            guild_refresh_poller, "get_dazebot_client", lambda: daze,
        )
        # also patch the module the poller imports the function from in case
        # the lookup happens via dazebot_module rather than guild_refresh_poller
        monkeypatch.setattr(dazebot_module, "get_dazebot_client", lambda: daze)
    if wapi is not None:
        monkeypatch.setattr(guild_refresh_poller, "get_wapi", lambda: wapi)


async def _mk_player(
    uuid: str,
    name: str,
    *,
    guild: str | None = None,
    tier: MembershipTier = MembershipTier.COMMUNITY,
    updated_at: datetime | None = None,
) -> AnniPlayer:
    p = await AnniPlayer.create(
        mc_uuid=uuid, mc_username=name,
        guild=guild, membership_tier=tier,
    )
    if updated_at is not None:
        # bypass auto_now by going through raw update; otherwise our staleness
        # tests can never set a row older than "now".
        await AnniPlayer.filter(mc_uuid=uuid).update(updated_at=updated_at)
        await p.refresh_from_db()
    return p


@pytest.fixture
def _settings() -> Settings:
    s = Settings()
    # Tiny floor so the floor-stale path is exercisable in tests.
    s.guild_refresh_floor_seconds = 60
    s.guild_refresh_call_cap_per_tick = 3
    return s


async def test_track_a_picks_up_returners_rejoin(db, _settings, monkeypatch):
    """The Piplup case: row exists with guild=NULL, tier=community; UUID
    now appears in the cached Returners roster. Track A flips it to
    guild='Returners' / tier=MEMBER on the next tick — zero outbound HTTP."""
    p = await _mk_player(
        "uuid-piplup", "PiplupMCFC",
        guild=None, tier=MembershipTier.COMMUNITY,
    )
    state = AppState(roster_by_uuid={"uuid-piplup": "PiplupMCFC"})

    daze = _FakeDazebot({})
    wapi = _FakeWapi({})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    await p.refresh_from_db()
    assert p.guild == "Returners"
    assert p.membership_tier == MembershipTier.MEMBER
    # Track A never touches the network.
    assert daze.calls == []
    assert wapi.calls == []


async def test_track_a_vetsmod_tier_overlay_to_honourary(
    db, _settings, monkeypatch,
):
    """Live vetsmod ``tier='honourary'`` on /v1/outbound/list (already merged
    into state.online_by_uuid) should reclassify a community row WITHOUT
    any check-snapshot call — the in-process signal is sufficient."""
    p = await _mk_player(
        "uuid-honourary", "Sevisoup",
        guild=None, tier=MembershipTier.COMMUNITY,
    )
    state = AppState(
        roster_by_uuid={"uuid-other": "Holidaze"},   # not in roster
        online_by_uuid={
            "uuid-honourary": OnlinePlayer(
                uuid="uuid-honourary", username="Sevisoup", tier="honourary",
            ),
        },
    )

    daze = _FakeDazebot({})
    wapi = _FakeWapi({})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    await p.refresh_from_db()
    assert p.membership_tier == MembershipTier.HONOURARY
    assert daze.calls == []
    assert wapi.calls == []


async def test_track_b_drift_clears_guild_when_left_returners(
    db, _settings, monkeypatch,
):
    """Row claims guild='Returners' but the cached roster disagrees. Track B
    fires: dazebot snapshot says they're not in Returners, WAPI player lookup
    returns no guild — row ends up guild=None / tier=COMMUNITY."""
    p = await _mk_player(
        "uuid-leaver", "FormerMember",
        guild="Returners", tier=MembershipTier.MEMBER,
    )
    state = AppState(roster_by_uuid={"uuid-other": "Holidaze"})  # not in roster

    daze = _FakeDazebot({
        "uuid-leaver": _Snap(in_returners_guild=False, discord_tier=None),
    })
    # WAPI returns a profile with no guild block — like an ex-Returners
    # member who's currently guildless.
    wapi = _FakeWapi({"uuid-leaver": {"username": "FormerMember", "guild": None}})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    await p.refresh_from_db()
    assert daze.calls == ["uuid-leaver"]
    assert wapi.calls == ["uuid-leaver"]
    assert p.guild is None
    assert p.membership_tier == MembershipTier.COMMUNITY


async def test_track_b_drift_keeps_returners_when_dazebot_confirms(
    db, _settings, monkeypatch,
):
    """Row claims guild='Returners', cached roster lags (e.g. tempserver one
    tick behind). Dazebot ``check_snapshot`` says in_returners_guild=True —
    so the row stays Returners/MEMBER and we DON'T spend a WAPI player call."""
    p = await _mk_player(
        "uuid-staystill", "StillReturners",
        guild="Returners", tier=MembershipTier.MEMBER,
    )
    state = AppState(roster_by_uuid={"uuid-other": "Holidaze"})

    daze = _FakeDazebot({
        "uuid-staystill": _Snap(in_returners_guild=True),
    })
    wapi = _FakeWapi({})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    await p.refresh_from_db()
    assert daze.calls == ["uuid-staystill"]
    # When dazebot confirms Returners, the WAPI player path is skipped.
    assert wapi.calls == []
    assert p.guild == "Returners"
    assert p.membership_tier == MembershipTier.MEMBER


async def test_floor_staleness_triggers_track_b(db, _settings, monkeypatch):
    """A row older than guild_refresh_floor_seconds gets a Track B refresh
    even when no drift signal applies (e.g. a community row never seen
    online and never logged into the dashboard since they linked Discord)."""
    old = datetime.now(timezone.utc) - timedelta(seconds=120)  # > 60s floor
    p = await _mk_player(
        "uuid-stale", "StaleGuy",
        guild=None, tier=MembershipTier.COMMUNITY, updated_at=old,
    )
    state = AppState(roster_by_uuid={"uuid-other": "Holidaze"})

    # Dazebot snapshot reveals they joined the waitlist via Discord — WAPI
    # would never know this.
    daze = _FakeDazebot({
        "uuid-stale": _Snap(
            in_returners_guild=False,
            discord_linked=True,
            discord_tier="waitlist",
        ),
    })
    wapi = _FakeWapi({"uuid-stale": {"guild": None}})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    await p.refresh_from_db()
    assert p.membership_tier == MembershipTier.WAITLIST
    assert daze.calls == ["uuid-stale"]


async def test_track_b_respects_per_tick_cap(db, _settings, monkeypatch):
    """When more rows need Track B than the cap allows, exactly cap calls
    are made — the rest wait for the next tick."""
    _settings.guild_refresh_call_cap_per_tick = 2

    old = datetime.now(timezone.utc) - timedelta(seconds=300)
    for i in range(5):
        await _mk_player(
            f"uuid-stale-{i}", f"Stale{i}",
            guild=None, tier=MembershipTier.COMMUNITY, updated_at=old,
        )
    state = AppState(roster_by_uuid={"uuid-other": "Holidaze"})

    # Default snapshot: not in returners, no tier — exercises only the cap.
    snaps = {
        f"uuid-stale-{i}": _Snap(in_returners_guild=False) for i in range(5)
    }
    daze = _FakeDazebot(snaps)
    wapi = _FakeWapi({f"uuid-stale-{i}": {"guild": None} for i in range(5)})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    assert len(daze.calls) == 2


async def test_empty_roster_bails_without_writes(db, _settings, monkeypatch):
    """When the cached roster is empty (boot, or tempserver wedged from the
    start) every row would falsely look like Returners drift. The poller
    must bail before any Track A / Track B work happens."""
    p = await _mk_player(
        "uuid-piplup", "PiplupMCFC",
        guild=None, tier=MembershipTier.COMMUNITY,
    )
    state = AppState()  # empty roster_by_uuid

    daze = _FakeDazebot({})
    wapi = _FakeWapi({})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    await p.refresh_from_db()
    assert p.guild is None
    assert p.membership_tier == MembershipTier.COMMUNITY
    assert daze.calls == []
    assert wapi.calls == []


async def test_dazebot_none_response_is_noop(db, _settings, monkeypatch):
    """Dazebot returning None (secret unset, transport error, …) for a
    drift row must NOT mutate the row — last-good stays served until the
    next floor sweep brings them back into the queue."""
    p = await _mk_player(
        "uuid-leaver", "FormerMember",
        guild="Returners", tier=MembershipTier.MEMBER,
    )
    state = AppState(roster_by_uuid={"uuid-other": "Holidaze"})

    daze = _FakeDazebot({"uuid-leaver": None})  # simulated failure
    wapi = _FakeWapi({})
    _patch_clients(monkeypatch, daze=daze, wapi=wapi)

    await guild_refresh_poller._tick(state, _settings)

    await p.refresh_from_db()
    assert daze.calls == ["uuid-leaver"]
    # Row preserved — we couldn't confirm departure.
    assert p.guild == "Returners"
    assert p.membership_tier == MembershipTier.MEMBER
