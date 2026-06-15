"""Anni-ping cog: magbot-webhook side of the role-ping flow.

Replaces dazebot's old ``cogs/moderation/anni.py``. When magbot posts the
trigger phrase in the stamp channel, this cog claims the per-event
``first_notice`` ping (DB-enforced unique constraint) and posts a bare
``<@&ROLE>`` to the same channel. The API-fallback path with the red
embed lives in :mod:`app.services.anni_ping_poller`; the DB row keeps
the two paths from double-pinging.

Components V2 + legacy content+embed are both supported — magbot's
auto-pings now arrive as Components V2 but manual operator pings still
use the legacy shape. Text-extraction helpers are copied from
temporary-server (no shared library between the two repos).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import discord
from discord.ext import commands
from tortoise.exceptions import IntegrityError

from app.db.lifecycle import get_active_event
from app.db.models import AnniEventPing
from app.settings import get_settings

logger = logging.getLogger("anni.fishbot.anni_ping")

_TRIGGER = "@Prelude to Annihilation"


def _embed_text_slots(embed: discord.Embed) -> Iterable[str]:
    """Yield text-bearing strings from a rich embed (every slot)."""
    if embed.title:
        yield embed.title
    if embed.description:
        yield embed.description
    if embed.author and embed.author.name:
        yield embed.author.name
    if embed.footer and embed.footer.text:
        yield embed.footer.text
    for field in embed.fields:
        if field.name:
            yield field.name
        if field.value:
            yield field.value


def _component_text_slots(components: Iterable[discord.Component]) -> Iterable[str]:
    """Yield text-bearing strings from a discord.py Components V2 tree.

    Only TextDisplay leaves and LabelComponent labels carry free-form prose
    that may contain the trigger. Separator/Media/File/Button/SelectMenu/
    ActionRow carry no relevant body text and are skipped.
    """
    for c in components:
        if isinstance(c, discord.TextDisplay):
            if c.content:
                yield c.content
        elif isinstance(c, discord.Container):
            yield from _component_text_slots(c.children)
        elif isinstance(c, discord.SectionComponent):
            yield from _component_text_slots(c.children)
            if c.accessory is not None:
                yield from _component_text_slots([c.accessory])
        elif isinstance(c, discord.LabelComponent):
            if c.label:
                yield c.label
            if c.component is not None:
                yield from _component_text_slots([c.component])


def _message_carries_trigger(message: discord.Message) -> bool:
    """True iff the trigger phrase appears anywhere magbot might put it."""
    if _TRIGGER in (message.content or ""):
        return True
    for embed in message.embeds:
        for text in _embed_text_slots(embed):
            if _TRIGGER in text:
                return True
    for text in _component_text_slots(message.components):
        if _TRIGGER in text:
            return True
    return False


class AnniPing(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        settings = get_settings()
        if (
            message.guild is None
            or message.channel.id != settings.anni_ping_channel_id
            or message.webhook_id != settings.anni_magbot_webhook_id
        ):
            return
        if not _message_carries_trigger(message):
            return

        active = await get_active_event()
        if active is None:
            # stamp_poller hasn't materialised an AnniEvent yet (its tick
            # races us). Drop this trigger silently; the next magbot post
            # or the poller's API-fallback path will pick it up after the
            # event row exists.
            logger.info(
                "anni_ping: magbot post seen but no active AnniEvent yet "
                "(message_id=%d) — skipping",
                message.id,
            )
            return

        try:
            await AnniEventPing.create(event=active, kind="first_notice")
        except IntegrityError:
            logger.debug(
                "anni_ping: first_notice already claimed for event %s — skipping",
                active.id,
            )
            return

        try:
            await message.channel.send(
                f"<@&{settings.anni_ping_role_id}>",
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            logger.info(
                "anni_ping: bare-pinged role %d for event %s (magbot trigger)",
                settings.anni_ping_role_id, active.id,
            )
        except discord.DiscordException:
            logger.exception(
                "anni_ping: failed to send bare ping for event %s "
                "(row already inserted — won't retry)", active.id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnniPing(bot))
