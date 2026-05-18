"""Tortoise / Aerich connection config.

``TORTOISE_ORM`` is module-level (Aerich imports it via ``aerich.ini``). The
DB URL is derived from :class:`app.settings.Settings` so the same code path
serves local dev (``./data/anni.db``) and Docker (``/app/data/anni.db`` via the
``ANNI_DB_PATH`` env).
"""

from __future__ import annotations

from app.settings import get_settings

_settings = get_settings()

#: Aerich + runtime read this. ``aerich.models`` must be registered so Aerich
#: can track its own migration bookkeeping table.
TORTOISE_ORM: dict = {
    "connections": {"default": _settings.db_url},
    "apps": {
        "models": {
            "models": ["app.db.models", "aerich.models"],
            "default_connection": "default",
        }
    },
    # Store tz-aware datetimes; we reason about countdowns in UTC.
    "use_tz": True,
    "timezone": "UTC",
}
