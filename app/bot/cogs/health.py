"""Trivial liveness cog: confirms fishbot reached READY. The real ``/rsvp``
command lands in Phase 3 (``app/bot/cogs/rsvp.py``)."""

from __future__ import annotations

import logging

from discord.ext import commands

logger = logging.getLogger("anni.fishbot")


class Health(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:  # pragma: no cover - network
        logger.info("fishbot health cog ready (guilds=%d)", len(self.bot.guilds))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Health(bot))
