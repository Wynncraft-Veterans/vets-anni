"""fishbot client + cog autoloader + lifecycle helpers.

The bot is optional at boot: if ``FISHBOT_TOKEN`` is unset (local dev, CI) the
app still runs fully — :func:`start_fishbot` just logs and returns. This keeps
the web app testable without Discord.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import pkgutil

import discord
from discord.ext import commands

from app.settings import get_settings

logger = logging.getLogger("anni.fishbot")


class FishBot(commands.Bot):
    """Minimal bot: slash-command tree + cog autoload from ``app.bot.cogs``."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Slash commands don't need privileged intents; keep the footprint tiny.
        super().__init__(command_prefix="!fish ", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        await self._load_cogs()
        # Global sync. Low command count; fine to sync on startup.
        try:
            await self.tree.sync()
        except Exception:  # pragma: no cover - network
            logger.exception("fishbot: app-command sync failed (non-fatal)")

    async def _load_cogs(self) -> None:
        """Import every ``app.bot.cogs.*`` module exposing ``async setup(bot)``
        (same convention as dazebot)."""
        import app.bot.cogs as cogs_pkg

        for mod in pkgutil.iter_modules(cogs_pkg.__path__):
            name = f"{cogs_pkg.__name__}.{mod.name}"
            try:
                module = importlib.import_module(name)
                if hasattr(module, "setup"):
                    await module.setup(self)
                    logger.info("fishbot: loaded cog %s", mod.name)
            except Exception:  # pragma: no cover - defensive
                logger.exception("fishbot: failed to load cog %s", name)

    async def on_ready(self) -> None:  # pragma: no cover - network
        logger.info("fishbot connected as %s (%s)", self.user, getattr(self.user, "id", "?"))


async def start_fishbot() -> tuple[FishBot | None, asyncio.Task | None]:
    """Start fishbot as a background task. Returns (bot, task) or (None, None)
    when no token is configured (the app keeps running regardless)."""
    token = get_settings().fishbot_token
    if not token:
        logger.warning("FISHBOT_TOKEN unset - fishbot disabled (web app still runs).")
        return None, None
    bot = FishBot()
    task = asyncio.create_task(bot.start(token), name="fishbot")
    return bot, task


async def stop_fishbot(bot: FishBot | None, task: asyncio.Task | None) -> None:
    if bot is not None:
        await bot.close()
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # pragma: no cover
            pass
