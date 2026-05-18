"""Shared test fixtures.

These deliberately reuse the work already done for local dev:

* the **dev data** — ``scripts/seed_dev.populate`` builds the realistic
  dataset; the ``seeded`` fixture loads it into an in-memory DB.
* the **dev server** — the ``client`` fixture drives the *real* ``main:app``
  ASGI app. ``httpx.ASGITransport`` does NOT emit lifespan events, so the
  app's ``lifespan`` (file DB + fishbot) never runs; routes instead use the
  in-memory ``db`` fixture. No Aerich, no Discord token, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[1]
# Make `app`, `main` and the (non-package) `scripts/` modules importable.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.db import lifecycle  # noqa: E402


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No unit test ever touches the network. The two outward calls identity
    resolution can make — the WAPI profile fetch and the Mojang last-resort —
    are stubbed to "nothing found" by default (fast + deterministic). Tests
    that need a specific profile/UUID still override this (a test-body
    ``monkeypatch`` / an injected ``mojang=`` runs *after* this fixture and
    wins — e.g. test_auth_flow, test_identity)."""
    from app.domain import identity
    from app.services import mojang

    async def _no_profile(_uuid):
        return None

    async def _no_mojang(_ign):
        return None

    monkeypatch.setattr(identity, "_fetch_wapi_profile", _no_profile)
    monkeypatch.setattr(mojang, "username_to_uuid", _no_mojang)


@pytest_asyncio.fixture
async def db():
    """A fresh in-memory schema per test (no Aerich, no file on disk)."""
    await lifecycle.init_for_tests()
    try:
        yield
    finally:
        await lifecycle.close()


@pytest_asyncio.fixture
async def seeded(db):
    """The dev dataset loaded into the in-memory DB.

    Returns ``populate()``'s handles: ``{"players": {name: AnniPlayer},
    "event": AnniEvent}``.
    """
    import seed_dev  # scripts/seed_dev.py, via sys.path above

    return await seed_dev.populate()


@pytest_asyncio.fixture
async def client(db):
    """ASGI client against the real app, backed by the in-memory ``db``."""
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
