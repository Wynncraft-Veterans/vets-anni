"""Low-trust login flow — IGN with/without password + the staff reset tool.

End-to-end against the in-memory DB but fully offline: the WAPI profile is
monkeypatched and Mojang is never reached (Alice resolves from the roster).
This is the Phase-1 "IGN login w/ + w/o password + staff reset" verification.
"""

from __future__ import annotations

import pytest

from app.db.models import AnniPlayer
from app.domain import identity
from app.services.state import AppState
from app.web import auth


@pytest.fixture
def state() -> AppState:
    return AppState(roster_by_uuid={"uuid-alice": "Alice"})


@pytest.fixture(autouse=True)
def _wapi_profile(monkeypatch):
    async def fake_profile(uuid):
        return {
            "username": "Alice",
            "guild": {"name": "Returners", "prefix": "VETS"},
            "lastJoin": "2026-05-10T00:00:00Z",
        }

    monkeypatch.setattr(identity, "_fetch_wapi_profile", fake_profile)


async def _fail_mojang(ign):  # roster must resolve Alice without Mojang
    return None


async def test_login_without_password_then_password_sticks(db, state):
    # First login, no password -> straight in, MEMBER (guild=Returners).
    out = await auth.login_user("Alice", "", state, mojang=_fail_mojang)
    assert out.ok and out.player is not None
    assert out.player.membership_tier.value == "member"
    assert out.player.password_hash is None

    # Set a password — it now "sticks".
    out = await auth.login_user("alice", "hunter2", state, mojang=_fail_mojang)
    assert out.ok
    player = await AnniPlayer.get(mc_uuid="uuid-alice")
    assert player.password_hash is not None

    # Subsequent login without/with-wrong password is refused (needs_password).
    out = await auth.login_user("Alice", "", state, mojang=_fail_mojang)
    assert not out.ok and out.needs_password
    out = await auth.login_user("Alice", "nope", state, mojang=_fail_mojang)
    assert not out.ok and out.needs_password

    # Correct password works.
    assert (await auth.login_user("Alice", "hunter2", state, mojang=_fail_mojang)).ok


async def test_staff_can_clear_a_stuck_password(db, state):
    await auth.login_user("Alice", "secret", state, mojang=_fail_mojang)
    player = await AnniPlayer.get(mc_uuid="uuid-alice")
    assert player.password_hash is not None

    assert await auth.clear_user_password("uuid-alice") is True

    # Cleared -> zero-friction login again (and a new password can be set).
    out = await auth.login_user("Alice", "", state, mojang=_fail_mojang)
    assert out.ok and out.player.password_hash is None


async def test_unknown_ign_is_a_friendly_failure_not_an_error(db):
    out = await auth.login_user("ghost", "", AppState(), mojang=_fail_mojang)
    assert not out.ok
    assert "Couldn't find" in (out.error or "")


async def test_staff_password_paths_are_failclosed(db):
    # No STAFF_PASSWORD / AppConfig hash -> never authenticates.
    assert await auth.check_staff_password("anything") is False
    # No ADMIN_PASSWORD configured -> rotation refused.
    assert await auth.rotate_staff_password("", "newpw") is False
