"""Schema safety-net.

Normal path: ``aerich upgrade`` (run by the Docker entrypoint) applies the
committed migrations. If ``migrations/`` is somehow absent (fresh clone before
the first migration is generated), this creates the schema directly so the app
still boots. Aerich remains the source of truth for real schema evolution.

    python -m app.db.bootstrap
"""

from __future__ import annotations

import asyncio
import logging

from tortoise import Tortoise

from app.db.config import TORTOISE_ORM
from app.db.lifecycle import init

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anni.db.bootstrap")


async def _main() -> None:
    await init()
    await Tortoise.generate_schemas(safe=True)
    await Tortoise.close_connections()
    log.info("bootstrap: schema ensured via generate_schemas(safe=True)")


if __name__ == "__main__":
    asyncio.run(_main())
