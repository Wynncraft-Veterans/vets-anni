"""``\\guess`` — predicted timing of the next annihilation.

Wraps the fitted inter-event model of (Uniform(71.4 h, 82.0 h) on
the most recent confirmed event in a Discord-public hybrid command.

Layering follows :mod:`app.bot.cogs.anni`: a pure ``execute_guess`` returns
a :class:`GuessReply` carrying the text + optional PNG bytes; a thin
:class:`GuessCog` shim drops it into Discord.

Timezone rule (CLAUDE.md): anni timing only ever appears as ``<t:N:F>`` /
``<t:N:R>`` tags so it localises per viewer — the box-plot image is
deliberately label-only (``Q0``…``Q4``), no wall-clock text baked in.
"""

from __future__ import annotations

import io
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import discord
from discord.ext import commands

from app.db.models import AnniEvent
from app.services.tempserver import get_tempserver
from app.settings import get_settings

# The plot is shape-only (Q0…Q4 spacing is fixed by the constants below), so
# we ship a pre-rendered PNG.
_BOX_PLOT_PNG = (
    Path(__file__).resolve().parent.parent / "resources" / "guess-box-plot.png"
).read_bytes()

logger = logging.getLogger("anni.fishbot.guess")


# Empirical anchor: anni #209 corresponds to stamp 1781012498.
_ANNI_ANCHOR_NUMBER = 209
_ANNI_ANCHOR_EPOCH = 1_781_012_498

# Quantile offsets in seconds, extrapolated from 472d of data.
# The window is Uniform(+71.4h, +82.0h)
# around the anchor; Q1/Q3 sit at 25/75% of that range.
_Q0_OFFSET = 257_040  # +71.4 h  earliest possible
_Q1_OFFSET = 266_400  # +74.0 h  25th percentile
_Q2_OFFSET = 276_120  # +76.7 h  median / mean
_Q3_OFFSET = 285_840  # +79.4 h  75th percentile
_Q4_OFFSET = 295_200  # +82.0 h  latest possible


@dataclass(frozen=True)
class GuessReply:
    """Pure return value from :func:`execute_guess`.

    ``image_png`` is ``None`` on the no-stamp and future-stamp branches —
    the cog uses that to decide whether to attach a :class:`discord.File`.
    """

    text: str
    image_png: bytes | None


def _dashboard_url() -> str:
    return get_settings().public_base_url.rstrip("/") + "/"


def _no_anchor_reply() -> GuessReply:
    return GuessReply(
        text=(
            "Nazbot broke, unable to make any predictions. This is a bug"
        ),
        image_png=None,
    )


async def _current_anni_number() -> int:
    """Anchor count + every newer ``AnniEvent`` row the poller has logged."""
    newer = await AnniEvent.filter(stamp_epoch__gt=_ANNI_ANCHOR_EPOCH).count()
    return _ANNI_ANCHOR_NUMBER + newer


def _format_confirmed(number: int, stamp_epoch: int) -> GuessReply:
    return GuessReply(
        text=(
            f"**Anni #{number} is confirmed**\n"
            f"Scheduled: <t:{stamp_epoch}:F> (<t:{stamp_epoch}:R>)"
        ),
        image_png=None,
    )


def _format_prediction(number: int, anchor: int) -> GuessReply:
    q0 = anchor + _Q0_OFFSET
    q1 = anchor + _Q1_OFFSET
    q2 = anchor + _Q2_OFFSET
    q3 = anchor + _Q3_OFFSET
    q4 = anchor + _Q4_OFFSET
    text = (
        f"## __**Anni Predictions for Anni #{number}**__\n"
        f"\n"
        f"# (Q₂) **Probable Time**: <t:{q2}:F> (<t:{q2}:R>)\n"
        f"\n"
        f"### __Other Possibilities__\n"
        f"(Q₀) **Earliest Possible**: <t:{q0}:F> (<t:{q0}:R>)\n"
        f"(Q₁) **Earliest Likely**: <t:{q1}:F> (<t:{q1}:R>)\n"
        f"(Q₃) **Latest Likely**: <t:{q3}:F> (<t:{q3}:R>)\n"
        f"(Q₄) **Latest Possible**: <t:{q4}:F> (<t:{q4}:R>)\n"
        f"\n"
        f"-# _Window is ≈10.6 h wide; the event is uniformly likely anywhere "
        f"inside [Q₀, Q₄]._"
    )
    return GuessReply(text=text, image_png=_BOX_PLOT_PNG)


async def execute_guess() -> GuessReply:
    """Pure executor: build the reply payload, no Discord side effects."""
    stamp_epoch = await get_tempserver().stamp()
    if stamp_epoch is None:
        return _no_anchor_reply()
    current_number = await _current_anni_number()
    if stamp_epoch > time.time():
        return _format_confirmed(current_number, stamp_epoch)
    return _format_prediction(current_number + 1, stamp_epoch)


async def _safe(fn: Callable[[], Awaitable[GuessReply]]) -> GuessReply:
    """Handle crashes."""
    try:
        return await fn()
    except Exception:  # noqa: BLE001
        logger.exception("`\\guess` handler crashed")
        return GuessReply(
            text="Something went wrong (fishbot bug).",
            image_png=None,
        )


class GuessCog(commands.Cog):
    """``\\guess`` / ``/guess`` — public anni prediction."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="guess",
        description="Predicted time of the next annihilation.",
    )
    async def guess(self, ctx: commands.Context) -> None:
        await ctx.defer()
        reply = await _safe(execute_guess)
        if reply.image_png is None:
            await ctx.reply(reply.text)
            return
        file = discord.File(
            io.BytesIO(reply.image_png), filename="anni-guess.png"
        )
        await ctx.reply(content=reply.text, file=file)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuessCog(bot))
