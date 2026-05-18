"""Database lifecycle + cross-cutting invariants.

* :func:`init` / :func:`close` — wrap Tortoise connect/disconnect. Schema is
  applied by ``aerich upgrade`` in the Docker entrypoint; :func:`init` only
  connects (and, for local dev convenience, creates the data directory).
* :func:`init_for_tests` — in-memory SQLite + ``generate_schemas`` so unit
  tests need no Aerich.
* :func:`get_active_event` / :func:`ensure_single_active` — enforce the
  "exactly one active :class:`AnniEvent`" invariant in code (SQLite has no
  partial-unique-index portability we want to rely on).
"""

from __future__ import annotations

import logging
import os

from tortoise import Tortoise

from app.db.config import TORTOISE_ORM
from app.db.models import AnniEvent
from app.settings import get_settings

logger = logging.getLogger("anni.db")


async def init() -> None:
    """Connect Tortoise using the runtime config. Assumes the schema is
    already migrated (entrypoint runs ``aerich upgrade``)."""
    settings = get_settings()
    db_path = settings.anni_db_path
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    await Tortoise.init(config=TORTOISE_ORM)
    logger.info("Database connected (%s)", settings.db_url)


async def init_for_tests() -> None:
    """Spin up an isolated in-memory DB with the full schema (no Aerich)."""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["app.db.models"]},
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas(safe=True)


async def close() -> None:
    await Tortoise.close_connections()


async def get_active_event() -> AnniEvent | None:
    """Return the single active event, or ``None`` when no anni is announced.

    ``organizer`` is eager-loaded (``select_related``) so templates can read
    ``event.organizer.mc_username`` without an awaited relation access (Jinja
    can't ``await``; a lazy FK there raises ``ParamsError``)."""
    return (
        await AnniEvent.filter(is_active=True)
        .select_related("organizer")
        .order_by("-announced_at")
        .first()
    )


async def ensure_single_active(event: AnniEvent) -> None:
    """Mark ``event`` active and demote any other active rows. Call inside a
    transaction when creating/rotating events."""
    await AnniEvent.filter(is_active=True).exclude(id=event.id).update(is_active=False)
    if not event.is_active:
        event.is_active = True
        await event.save(update_fields=["is_active"])
