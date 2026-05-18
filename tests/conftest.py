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

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[1]
# Make `app`, `main` and the (non-package) `scripts/` modules importable.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.db import lifecycle  # noqa: E402


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
