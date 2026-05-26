"""``\\anni`` group — public read-only views of the announced anni.

Mirrors :mod:`app.bot.cogs.rsvp` in shape: a pure-async ``execute_*`` per
subcommand returns the message string, and a thin :class:`AnniCog`
hybrid-group shim renders it. Every reply is **public** (no ephemeral
deferral) — these are status reads, not personal data.

Timezone rule (CLAUDE.md): anni timing is *only* referenced via Discord
timestamp tags (``<t:N:R>`` / ``<t:N:F>``) so it localises per viewer —
never "tonight" / "today" / a wall-clock string.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from discord.ext import commands

from app.constants import PARTY_CAPACITY, PARTY_STAGE_LABELS
from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer, Rsvp
from app.domain import buckets
from app.domain.roles import guidance
from app.domain.schedule import EventPhase, phase_of
from app.services.dazebot_client import get_dazebot_client
from app.settings import get_settings

logger = logging.getLogger("anni.fishbot.anni")


_PHASE_LABEL: dict[EventPhase, str] = {
    EventPhase.PENDING: "Pending",
    EventPhase.GRACE: "In grace (fight in progress; recording results)",
    EventPhase.EXPIRED: "Expired (about to be wiped)",
}


def _dashboard_url() -> str:
    return get_settings().public_base_url.rstrip("/") + "/"


def _no_event_message() -> str:
    return (
        f"No anni is currently announced. Watch the dashboard: {_dashboard_url()}"
    )


async def execute_status() -> str:
    """``\\anni status`` — countdown, phase, organiser, summary counts."""
    event = await get_active_event()
    if event is None:
        return _no_event_message()
    settings = get_settings()
    grace_seconds = max(0, settings.grace_hours) * 3600
    phase = phase_of(event.stamp_epoch, grace_seconds)
    parties = await buckets.parties_of(event)
    rows = await buckets.board_rows(event)
    rsvp_count = await Rsvp.filter(event=event, revoked_at__isnull=True).count()
    organiser = event.organizer.mc_username if event.organizer else "_unclaimed_"
    stamp = event.stamp_epoch
    return (
        f"**Next anni:** <t:{stamp}:F> (<t:{stamp}:R>)\n"
        f"**Phase:** {_PHASE_LABEL[phase]}\n"
        f"**Organiser:** {organiser}\n"
        f"**Parties:** {len(parties)} · **On the board:** {len(rows)} · "
        f"**Active RSVPs:** {rsvp_count}\n"
        f"Dashboard: {_dashboard_url()}"
    )


async def execute_parties() -> str:
    """``\\anni parties`` — per-party stage/result/host/world breakdown."""
    event = await get_active_event()
    if event is None:
        return _no_event_message()
    parties = await buckets.parties_of(event)
    stamp = event.stamp_epoch
    if not parties:
        return (
            f"No parties created yet for the anni at <t:{stamp}:F> "
            f"(<t:{stamp}:R>). Dashboard: {_dashboard_url()}"
        )
    rows = await buckets.board_rows(event)
    counts: dict[str, int] = {}
    for r in rows:
        if r["party_id"]:
            counts[r["party_id"]] = counts.get(r["party_id"], 0) + 1
    lines = [f"**Parties for the anni at <t:{stamp}:F> (<t:{stamp}:R>):**"]
    for p in parties:
        members = counts.get(str(p.id), 0)
        host = p.host.mc_username if p.host else "_no host_"
        world = p.world or "_world TBD_"
        stage_label = PARTY_STAGE_LABELS.get(p.stage, "")
        lines.append(
            f"**Party {p.ordinal}** — Stage {p.stage}/5 · "
            f"**{p.result.value.upper()}** · {members}/{PARTY_CAPACITY} · "
            f"host: `{host}` · world: `{world}`"
        )
        if stage_label:
            lines.append(f"  _{stage_label}_")
    lines.append(f"Dashboard: {_dashboard_url()}")
    return "\n".join(lines)


async def execute_roles(discord_id: int | str) -> str:
    """``\\anni roles`` — the invoker's declared capabilities (read-only).

    Identity is resolved through the same dazebot path as ``/rsvp`` so a
    player who has never logged into the dashboard still gets a sensible
    answer. The fetch is **read-only** (no get-or-create) — Discord must
    never author the AnniPlayer row from this command.
    """
    identity = await get_dazebot_client().resolve_anni_identity(discord_id)
    if identity is None:
        return (
            "The identity service is unavailable right now. Try again in a "
            "minute."
        )
    if identity.blocked:
        suffix = f" ({identity.reason})" if identity.reason else ""
        return (
            f"Your dazebot link is currently blocked{suffix}. Speak to staff."
        )
    if not identity.linked or not identity.mc_uuid:
        return (
            "I can't see a Minecraft account linked to your Discord — "
            "verify with dazebot (`~verify`) and try again."
        )

    player = await (
        AnniPlayer.filter(mc_uuid=identity.mc_uuid)
        .prefetch_related("capabilities__weapons")
        .first()
    )
    me_url = _dashboard_url() + "me"
    caps = list(player.capabilities) if player is not None else []
    if not caps:
        who = identity.mc_username or "you"
        return (
            f"`{who}` hasn't declared any role capabilities yet. "
            f"Add them on the dashboard: {me_url}"
        )

    caps.sort(key=lambda c: c.role.value)
    lines = [
        f"**Declared roles for `{player.mc_username}`** "
        f"(read-only — edit them on {me_url}):"
    ]
    for c in caps:
        weapons = (
            ", ".join(f"`{w.weapon_name}`" for w in c.weapons)
            or "_no weapons listed_"
        )
        lines.append(
            f"• **{guidance(c.role).title}** — confidence "
            f"`{c.confidence.value}`, build `{c.build_quality.value}`, "
            f"lifetime wins {c.success_count}\n"
            f"  weapons: {weapons}"
        )
    return "\n".join(lines)


async def _safe(fn: Callable[..., Awaitable[str]], *args) -> str:
    """Run an ``execute_*`` and turn an unexpected crash into a friendly
    string. The executes are defensive (no raises on the happy paths), so
    this is purely a belt-and-braces seatbelt for the cog shim."""
    try:
        return await fn(*args)
    except Exception:  # noqa: BLE001
        logger.exception("`\\anni` handler crashed")
        return "Something went wrong — staff have been notified."


class AnniCog(commands.Cog):
    """``\\anni`` hybrid group (and ``/anni …`` slash form)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="anni", description="Read-only anni info.")
    async def anni_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(
                "Use `\\anni status` / `parties` / `roles` "
                "(or the `/anni …` slash form).",
                ephemeral=True,
            )

    @anni_group.command(
        name="status",
        description="Countdown + phase + party/RSVP counts (public).",
    )
    async def status(self, ctx: commands.Context) -> None:
        await self._public_reply(ctx, await _safe(execute_status))

    @anni_group.command(
        name="parties",
        description="Per-party stage/result/host/world breakdown (public).",
    )
    async def parties(self, ctx: commands.Context) -> None:
        await self._public_reply(ctx, await _safe(execute_parties))

    @anni_group.command(
        name="roles",
        description="Your declared role capabilities (public, read-only).",
    )
    async def roles(self, ctx: commands.Context) -> None:
        await self._public_reply(ctx, await _safe(execute_roles, ctx.author.id))

    async def _public_reply(self, ctx: commands.Context, message: str) -> None:
        # Public = non-ephemeral. Defer keeps a slow dazebot lookup from
        # timing out the slash interaction; on a prefix invocation defer
        # is a no-op.
        await ctx.defer()
        await ctx.reply(message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnniCog(bot))
