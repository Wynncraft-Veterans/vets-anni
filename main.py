"""vets-anni entrypoint — one process: FastAPI + fishbot + pollers.

Boots the web app, connects the DB, starts fishbot, and (Phase 1+) runs the
upstream pollers as lifespan asyncio tasks sharing one :class:`AppState`.

The shared cache is created in :func:`create_app` (not the lifespan) so the
test ASGI transport — which skips lifespan — still has ``app.state.appstate``;
routes then read an empty-but-valid cache instead of erroring.

Run locally:   python main.py        (serves http://127.0.0.1:8000)
Schema:        aerich upgrade        (Docker entrypoint does this automatically)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

load_dotenv()  # local dev convenience; prod injects real env via the stack .env

from app.bot.client import start_fishbot, stop_fishbot  # noqa: E402
from app.db import lifecycle  # noqa: E402
from app.services import (  # noqa: E402
    api_disabled,
    auto_promoter,
    dazebot_client,
    lifecycle_task,
    mojang,
    online_merge,
    presence_poller,
    staff_poller,
    stamp_poller,
    weapons_poller,
)
from app.services.state import AppState  # noqa: E402
from app.services.tempserver import get_tempserver  # noqa: E402
from app.services.wapi import get_wapi  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.web.routers import (  # noqa: E402
    capability,
    organizer,
    public,
    roles_dash,
    staff,
    staff_capability,
    user,
)

logging.basicConfig(
    level=logging.DEBUG if get_settings().debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Quiet third-party DEBUG noise that drowns out our own logs: full SQL strings
# from tortoise + the same query repeated by aiosqlite, plus discord's gateway
# heartbeats and frame dumps. Our own ``anni.*`` loggers stay at DEBUG.
for _noisy in ("tortoise.db_client", "aiosqlite", "discord.gateway", "discord.client"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("anni")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown. Pollers share ``app.state.appstate``."""
    await lifecycle.init()
    bot, bot_task = await start_fishbot()
    app.state.fishbot = bot

    settings = get_settings()
    state: AppState = app.state.appstate
    app.state.poller_tasks = [
        asyncio.create_task(stamp_poller.run(state, settings), name="stamp"),
        asyncio.create_task(staff_poller.run(state, settings), name="staff"),
        asyncio.create_task(online_merge.run(state, settings), name="online"),
        asyncio.create_task(weapons_poller.run(state, settings), name="weapons"),
        # Phase 2: live board status + the api-disabled probe + grace/wipe.
        asyncio.create_task(presence_poller.run(state, settings), name="presence"),
        asyncio.create_task(api_disabled.run(state, settings), name="apidisabled"),
        asyncio.create_task(lifecycle_task.run(state, settings), name="lifecycle"),
        # Phase 3: spec.md "auto-populated from RSVP or 1hr-early".
        asyncio.create_task(auto_promoter.run(state, settings), name="autopromoter"),
    ]
    logger.info("vets-anni started (%d pollers)", len(app.state.poller_tasks))
    try:
        yield
    finally:
        for task in app.state.poller_tasks:
            task.cancel()
        await asyncio.gather(*app.state.poller_tasks, return_exceptions=True)
        await stop_fishbot(bot, bot_task)
        await get_wapi().close()
        await get_tempserver().close()
        await dazebot_client.close()
        await mojang.close()
        await lifecycle.close()
        logger.info("vets-anni stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="vets-anni", lifespan=lifespan, docs_url=None, redoc_url=None)
    # Created here (not in lifespan) so the test ASGI transport has it too.
    app.state.appstate = AppState()
    app.state.poller_tasks = []
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(public.router)
    app.include_router(user.router)
    app.include_router(capability.router)
    app.include_router(staff.router)
    app.include_router(organizer.router)
    app.include_router(roles_dash.router)
    app.include_router(staff_capability.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=get_settings().debug,
    )
