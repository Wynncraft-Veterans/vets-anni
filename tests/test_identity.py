"""Identity resolution — the UUID anchor logic, fully offline.

Network is never touched: the cache step is pure, Mojang is an injected stub,
and the WAPI profile fetch is monkeypatched. Covers the resolution order
(roster -> aliases -> Mojang), rename desync, and the api-disabled sentinel.
"""

from __future__ import annotations

import pytest

from app.domain import identity
from app.domain.identity import EPOCH, IdentityResult
from app.services.state import AppState

ROSTER = {"uuid-alice": "Alice", "uuid-bob": "Bob"}
ALIASES = {"oldbob": "uuid-bob"}


def test_resolve_uuid_cached_order_and_case_insensitivity():
    assert identity.resolve_uuid_cached("alice", ROSTER, ALIASES) == "uuid-alice"
    assert identity.resolve_uuid_cached("ALICE", ROSTER, ALIASES) == "uuid-alice"
    # Not in roster but a known legacy name -> alias hit.
    assert identity.resolve_uuid_cached("OldBob", ROSTER, ALIASES) == "uuid-bob"
    assert identity.resolve_uuid_cached("nobody", ROSTER, ALIASES) is None
    assert identity.resolve_uuid_cached("", ROSTER, ALIASES) is None


def test_dash_uuid_normalises_only_bare_32_hex():
    bare = "0123456789abcdef0123456789abcdef"
    assert identity.dash_uuid(bare) == "01234567-89ab-cdef-0123-456789abcdef"
    dashed = "01234567-89ab-cdef-0123-456789abcdef"
    assert identity.dash_uuid(dashed) == dashed
    assert identity.dash_uuid("short") == "short"


def test_is_api_disabled_sentinel():
    from datetime import datetime, timezone

    assert identity.is_api_disabled(None) is True
    assert identity.is_api_disabled(EPOCH) is True
    assert identity.is_api_disabled(datetime.now(timezone.utc)) is False


async def test_resolve_identity_uses_roster_then_wapi(monkeypatch):
    async def fake_profile(uuid):
        return {
            "username": "Alice",
            "guild": {"name": "Returners", "prefix": "VETS"},
            "lastJoin": "2026-05-01T12:00:00.000Z",
        }

    monkeypatch.setattr(identity, "_fetch_wapi_profile", fake_profile)
    state = AppState(roster_by_uuid=dict(ROSTER))

    res = await identity.resolve_identity("alice", state, mojang=_no_mojang)
    assert isinstance(res, IdentityResult)
    assert res.mc_uuid == "uuid-alice"
    assert res.in_returners_roster is True
    assert res.guild_name == "Returners" and res.guild_tag == "VETS"
    assert not identity.is_api_disabled(res.last_online)


async def test_resolve_identity_falls_back_to_mojang_and_handles_no_wapi(monkeypatch):
    async def no_profile(uuid):
        return None

    async def mojang(ign):
        return "uuid-zed" if ign.lower() == "zed" else None

    monkeypatch.setattr(identity, "_fetch_wapi_profile", no_profile)
    state = AppState()  # empty caches -> must use Mojang

    res = await identity.resolve_identity("Zed", state, mojang=mojang)
    assert res is not None
    assert res.mc_uuid == "uuid-zed"
    assert res.mc_username == "Zed"          # best-effort: the typed IGN
    assert identity.is_api_disabled(res.last_online)  # no lastJoin -> sentinel
    assert res.in_returners_roster is False

    # Unknown IGN everywhere -> None (login surfaces a friendly error).
    assert await identity.resolve_identity("ghost", state, mojang=mojang) is None


async def _no_mojang(ign: str) -> str | None:
    raise AssertionError("Mojang must not be called when the roster resolves it")


async def test_mark_registered_flips_true_to_false(seeded):
    """One-way flip: placeholder -> registered, persisted."""
    from app.db.models import AnniPlayer
    p = await AnniPlayer.create(
        mc_uuid="uuid-stubmark", mc_username="Stub", is_placeholder=True,
    )
    flipped = await identity.mark_registered(p)
    assert flipped is True
    await p.refresh_from_db()
    assert p.is_placeholder is False


async def test_mark_registered_noop_when_already_registered(seeded):
    """Calling on a non-placeholder is a cheap no-op (no DB write)."""
    from app.db.models import AnniPlayer
    p = await AnniPlayer.create(
        mc_uuid="uuid-realmark", mc_username="Real", is_placeholder=False,
    )
    flipped = await identity.mark_registered(p)
    assert flipped is False
    await p.refresh_from_db()
    assert p.is_placeholder is False
