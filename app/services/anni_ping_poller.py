"""Anni-ping poller: API-fallback first-notice + T-90 reminder.

This is the half of the anni-ping flow that does NOT depend on magbot.
When the temp-server's stamp feed surfaces a future anni (regardless of
source — webhook, beta-api, or api), :class:`AnniEvent` gets created by
``stamp_poller``. This poller then watches it and inserts the two
allowed pings per occurrence:

* **first_notice** — magbot's own ping is sent by
  :mod:`app.bot.cogs.anni_ping`. This poller's job is to take over when
  magbot is silent: after a short grace window past
  ``AnniEvent.announced_at`` with no first_notice row inserted, fire the
  "Hateful echoes erupt from the Portal" red embed + role ping.
* **t_minus_90** — at ``now >= stamp_epoch - 90 min``, fire the
  "Annihilation is coming" red embed + role ping. Independent of whether
  first_notice came from magbot or the fallback path above.

Cap is enforced by the ``unique_together = (event, kind)`` constraint on
:class:`AnniEventPing`: a tight cog-vs-poller race resolves cleanly
because the loser hits ``IntegrityError`` before sending. Cold-boot with
an already-active event "just works" because the rule is *state-based*
(does the row exist? if not, the ping is owed) rather than event-driven.

Cold-boot edge case: if first_notice has not yet been inserted but the
event is already inside the T-90 window (e.g. magbot was silent and
fishbot only just came online with <90 min to go), the poller inserts
the first_notice row as *bookkeeping only* — the t_minus_90 ping is the
more urgent message and is sent on the same tick; sending the
first_notice embed in addition would just be back-to-back spam.
"""

from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands
from tortoise.exceptions import IntegrityError

from app.db.lifecycle import get_active_event
from app.db.models import AnniEvent, AnniEventPing
from app.services.loop import poll_forever
from app.services.state import AppState
from app.settings import Settings

logger = logging.getLogger("anni.anni_ping_poller")


_FIRST_NOTICE_DESCRIPTION = (
    "*Hateful echoes erupt from the Portal.*\n"
    "***Wynn faces Annihilation in <t:{stamp}:R>!***\n"
    "\n"
    "-# (Time to `\\rsvp`! Make sure you have set your `\\anni roles`!)"
)

_T_MINUS_90_DESCRIPTION = (
    "*Something from the other side roars through the portal.*\n"
    "***Annihilation is coming in <t:{stamp}:R>!***\n"
    "\n"
    "-# (Make your way to the server, and follow along on https://anni.wynnvets.org/me)\n"
    "-# For more info on our party formation status, you can also do `\\anni status`)"
)


def _build_embed(stamp_epoch: int, description_template: str) -> discord.Embed:
    return discord.Embed(
        title="Prelude to Annihilation",
        description=description_template.format(stamp=stamp_epoch),
        colour=discord.Colour.red(),
    )


async def _has_ping(event: AnniEvent, kind: str) -> bool:
    return await AnniEventPing.filter(event=event, kind=kind).exists()


async def _claim_ping(event: AnniEvent, kind: str) -> bool:
    """Insert the (event, kind) row. Returns True iff we won the race."""
    try:
        await AnniEventPing.create(event=event, kind=kind)
    except IntegrityError:
        return False
    return True


async def _send(
    bot: commands.Bot, channel_id: int, role_id: int, embed: discord.Embed,
) -> None:
    """Post the red embed, then the role ping in a follow-up. Fire-and-forget
    for the role mention so a temporary 5xx on the ping doesn't undo the
    already-sent embed (the DB row is in either way; this is best-effort)."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        # `fetch_channel` is the network fallback; `get_channel` only hits
        # the cache. A fresh boot may have no cache for this channel yet.
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException:
            logger.exception(
                "anni_ping_poller: channel %d not reachable", channel_id,
            )
            return
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        logger.warning(
            "anni_ping_poller: channel %d is not a text channel (%s)",
            channel_id, type(channel).__name__,
        )
        return
    try:
        await channel.send(embed=embed)
        await channel.send(
            f"<@&{role_id}>",
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
    except discord.DiscordException:
        logger.exception(
            "anni_ping_poller: failed to send to channel %d", channel_id,
        )


async def _tick(
    state: AppState, settings: Settings, bot: commands.Bot | None,
) -> None:
    event = await get_active_event()
    if event is None:
        return

    now = int(time.time())
    if event.stamp_epoch <= now:
        # Past/now: nothing to ping for — the event has already started
        # (or just did) and the t_minus_90 window is also in the past.
        return

    inside_t90 = now >= event.stamp_epoch - settings.anni_t_minus_90_seconds
    past_grace = (
        time.time() - event.announced_at.timestamp()
        >= settings.anni_first_notice_grace_seconds
    )
    has_first_notice = await _has_ping(event, "first_notice")
    has_t_minus_90 = await _has_ping(event, "t_minus_90")

    # T-90 first: it's the more urgent message and short-circuits the
    # first-notice posting decision below.
    if inside_t90 and not has_t_minus_90:
        if await _claim_ping(event, "t_minus_90"):
            logger.info(
                "anni_ping_poller: firing t_minus_90 for event %s (stamp=%d, now=%d)",
                event.id, event.stamp_epoch, now,
            )
            if bot is not None:
                await _send(
                    bot,
                    settings.anni_ping_channel_id,
                    settings.anni_ping_role_id,
                    _build_embed(event.stamp_epoch, _T_MINUS_90_DESCRIPTION),
                )
            has_t_minus_90 = True

    # First-notice fallback (magbot was silent or the API source beat it).
    if past_grace and not has_first_notice:
        if await _claim_ping(event, "first_notice"):
            if inside_t90:
                # Cold-boot inside T-90: bookkeeping only, the t_minus_90
                # ping is the user-visible announcement.
                logger.info(
                    "anni_ping_poller: claimed first_notice for event %s "
                    "but inside T-90 — suppressing embed (t_minus_90 carries "
                    "the announcement)",
                    event.id,
                )
            else:
                logger.info(
                    "anni_ping_poller: firing first_notice for event %s "
                    "(stamp=%d, magbot did not claim within %ds grace)",
                    event.id, event.stamp_epoch,
                    settings.anni_first_notice_grace_seconds,
                )
                if bot is not None:
                    await _send(
                        bot,
                        settings.anni_ping_channel_id,
                        settings.anni_ping_role_id,
                        _build_embed(
                            event.stamp_epoch, _FIRST_NOTICE_DESCRIPTION,
                        ),
                    )


async def run(
    state: AppState, settings: Settings, bot: commands.Bot | None,
) -> None:
    await poll_forever(
        "annipings",
        lambda: float(settings.anni_ping_poll_seconds),
        lambda: _tick(state, settings, bot),
    )
