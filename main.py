"""vets-anni entrypoint — one process: FastAPI + fishbot + pollers.

Boots the web app, connects the DB, and starts fishbot as a background task on
the same event loop (lean for the 2 GB VPS). Pollers register here from
Phase 1 onward; Phase 0 just proves the skeleton boots and is styled.

Run locally:   python main.py        (serves http://127.0.0.1:8000)
Schema:        aerich upgrade        (Docker entrypoint does this automatically)
"""

from __future__ import annotations

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
from app.settings import get_settings  # noqa: E402
from app.web.routers import public  # noqa: E402

logging.basicConfig(
    level=logging.DEBUG if get_settings().debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("anni")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown. Pollers (Phase 1+) attach to ``app.state`` here."""
    await lifecycle.init()
    bot, bot_task = await start_fishbot()
    app.state.fishbot = bot
    app.state.poller_tasks = []  # populated from Phase 1
    logger.info("vets-anni started")
    try:
        yield
    finally:
        for task in app.state.poller_tasks:
            task.cancel()
        await stop_fishbot(bot, bot_task)
        await lifecycle.close()
        logger.info("vets-anni stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="vets-anni", lifespan=lifespan, docs_url=None, redoc_url=None)
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(public.router)
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
