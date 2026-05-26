"""``/rsvp <hard|soft|revoke|status>`` — fishbot's only user-facing command.

The Discord side is a thin wrapper. The actual decision tree lives in
:func:`execute_rsvp` so the same path is reachable from unit tests without
spinning up a real :class:`discord.Interaction`. The cog only does what only
Discord can do: defer the response, send the ephemeral reply, and post the
concise *public* confirmation line to ``RSVP_CHANNEL_ID`` (the spec's
"visibility/record ack" — accept/reject outcomes are surfaced via the
dashboard, not back through fishbot).

Identity flow:

1. POST the invoking Discord snowflake to dazebot's secret-gated
   ``/api/internal/anni-identity`` (``app.services.dazebot_client``).
2. If dazebot is unreachable -> graceful "service unavailable" ephemeral
   reply (no DB writes, no public line). ``/rsvp`` MUST degrade gracefully
   (``.claude/integration.md``); we never crash on a missing link.
3. Once we have the MC UUID, get-or-create the :class:`AnniPlayer` row
   (fishbot users may be brand-new to vets-anni — the web side won't have
   seen them yet).
4. Hand to :mod:`app.domain.rsvp` (the sole Rsvp writer).

Membership tier on a *new* fishbot-originated player row comes from
dazebot's resolved tier (the only authoritative source for waitlist /
honourary — the web path can't see Discord-only signals); existing rows are
left alone so the regular pollers stay authoritative.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import discord
from discord.ext import commands

from app.constants import (
    BUCKET_LABEL,
    AttendanceNotice,
    MembershipTier,
)
from app.db.lifecycle import get_active_event
from app.db.models import (
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    RoleCapability,
    Rsvp,
)
from app.domain import capability as capability_domain
from app.domain import identity as identity_domain
from app.domain import rsvp as rsvp_domain
from app.domain.membership import _DAZEBOT_TIER
from app.domain.membership import label as tier_label
from app.domain.roles import guidance as role_guidance
from app.services.dazebot_client import AnniIdentity, get_dazebot_client
from app.services.state import AppState
from app.settings import get_settings

logger = logging.getLogger("anni.fishbot.rsvp")

#: The four subcommand names — also the action strings carried through
#: :func:`execute_rsvp`. Keep these aligned with the spec wording.
Action = Literal["hard", "soft", "revoke", "status"]


@dataclass(frozen=True)
class RsvpOutcome:
    """Structured result of one ``/rsvp`` invocation.

    ``private_message`` is always sent (ephemerally) back to the invoker;
    ``public_message`` is sent to ``RSVP_CHANNEL_ID`` only when non-``None``
    (mutations announce; ``status`` does not).
    """

    private_message: str
    public_message: str | None = None


def _dashboard_url() -> str:
    """Public dashboard URL the user can click from any reply."""
    return get_settings().public_base_url.rstrip("/") + "/"


def _notice_label(notice: AttendanceNotice) -> str:
    """Human-readable label for an RSVP notice (used in replies)."""
    return "HARD" if notice is AttendanceNotice.RSVP_HARD else "SOFT"


async def _upsert_player(identity: AnniIdentity) -> AnniPlayer:
    """Get-or-create the AnniPlayer row from a linked dazebot identity.

    On create, seed ``membership_tier`` from dazebot's tier (the only place
    waitlist/honourary become visible to this app). On an existing row we
    refresh ``mc_username`` (rename desync) but leave everything else alone —
    the pollers own guild/last_online/tier-from-guild.
    """
    assert identity.mc_uuid is not None  # only call after linked-check
    tier_from_daze = _DAZEBOT_TIER.get((identity.tier or "").lower())
    player, created = await AnniPlayer.get_or_create(
        mc_uuid=identity.mc_uuid,
        defaults={
            "mc_username": identity.mc_username or identity.mc_uuid,
            "wynn_username": identity.mc_username or identity.mc_uuid,
            "membership_tier": tier_from_daze or MembershipTier.OTHER,
        },
    )
    if not created and identity.mc_username and player.mc_username != identity.mc_username:
        player.mc_username = identity.mc_username
        await player.save(update_fields=["mc_username", "updated_at"])
    return player


async def _render_status(player: AnniPlayer, event: AnniEvent) -> RsvpOutcome:
    current = await rsvp_domain.get_current(player, event)
    url = _dashboard_url()
    if current is None:
        msg = (
            f"You have no active RSVP. Use `/rsvp hard` or `/rsvp soft` to "
            f"commit, or check your dashboard: {url}"
        )
    else:
        msg = (
            f"Your current RSVP is **{_notice_label(current.notice)}** "
            f"(set <t:{int(current.updated_at.timestamp())}:R>). Dashboard: {url}"
        )
    return RsvpOutcome(private_message=msg)


async def _do_set(
    player: AnniPlayer, event: AnniEvent, notice: AttendanceNotice
) -> RsvpOutcome:
    await rsvp_domain.set_rsvp(player, event, notice)
    label = _notice_label(notice)
    url = _dashboard_url()
    private = (
        f"RSVP recorded: **{label}**. Track your status on the dashboard: {url}"
    )
    # Discord timestamp tags only — anni timing must localise per viewer
    # (CLAUDE.md). The earlier wording "for tonight's anni" was wrong for
    # anyone outside the organisers' wall-clock evening.
    public = (
        f"`{player.mc_username}` has **{label}** RSVP'd for the anni "
        f"<t:{event.stamp_epoch}:R> (<t:{event.stamp_epoch}:F>)."
    )
    return RsvpOutcome(private_message=private, public_message=public)


async def _do_revoke(player: AnniPlayer, event: AnniEvent) -> RsvpOutcome:
    prior = await rsvp_domain.revoke(player, event)
    url = _dashboard_url()
    if prior is None:
        # No active RSVP — be friendly and don't spam the public channel.
        return RsvpOutcome(
            private_message=(
                f"You had no active RSVP to withdraw. Dashboard: {url}"
            )
        )
    return RsvpOutcome(
        private_message=(
            f"Your **{_notice_label(prior.notice)}** RSVP has been withdrawn. "
            f"Dashboard: {url}"
        ),
        public_message=f"`{player.mc_username}` withdrew their RSVP.",
    )


async def execute_rsvp(discord_id: int | str, action: Action) -> RsvpOutcome:
    """The full decision tree, callable without a real Discord interaction.

    Returns a :class:`RsvpOutcome` for every input (no exceptions thrown);
    the cog renders it. Tests drive this directly.
    """
    identity = await get_dazebot_client().resolve_anni_identity(discord_id)
    if identity is None:
        return RsvpOutcome(
            private_message=(
                "The identity service is unavailable right now — your RSVP "
                "wasn't changed. Try again in a minute."
            )
        )
    if identity.blocked:
        return RsvpOutcome(
            private_message=(
                f"Your dazebot link is currently blocked"
                f"{f' ({identity.reason})' if identity.reason else ''}. "
                "Speak to staff."
            )
        )
    if not identity.linked or not identity.mc_uuid:
        return RsvpOutcome(
            private_message=(
                "I can't see a Minecraft account linked to your Discord — "
                "verify with dazebot (`~verify`) and try again."
            )
        )

    event = await get_active_event()
    if event is None:
        url = _dashboard_url()
        return RsvpOutcome(
            private_message=(
                f"No anni is currently announced — there's nothing to RSVP "
                f"for. Watch the dashboard: {url}"
            )
        )

    player = await _upsert_player(identity)

    if action == "status":
        return await _render_status(player, event)
    if action == "revoke":
        return await _do_revoke(player, event)
    if action == "hard":
        return await _do_set(player, event, AttendanceNotice.RSVP_HARD)
    if action == "soft":
        return await _do_set(player, event, AttendanceNotice.RSVP_SOFT)
    # The Group's subcommand wiring prevents this from being reachable.
    raise ValueError(f"unknown /rsvp action: {action!r}")


# --------------------------------------------------------------------------- #
# Read-only subcommands: ``\rsvp list`` + ``\rsvp check <username>``          #
# --------------------------------------------------------------------------- #
#
# These two are public-reply (no ephemeral), do NOT need the dazebot identity
# round-trip, and never call WAPI/Mojang — IGN lookup is cache-first (the
# OWN-token bucket must not be spent from Discord; CLAUDE.md hard rule).


async def _resolve_player_by_ign(name: str, state: AppState) -> AnniPlayer | None:
    """Cache-first IGN → :class:`AnniPlayer` (no network).

    Tries, in order: an exact-case-insensitive match on
    ``AnniPlayer.mc_username``, then the roster/aliases cache on
    :class:`AppState`. Returns ``None`` when neither knows the name —
    callers render a friendly miss rather than spending the WAPI token.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    player = await AnniPlayer.filter(mc_username__iexact=cleaned).first()
    if player is not None:
        return player
    uuid = state.resolve_uuid(cleaned)
    if uuid is None:
        return None
    return await AnniPlayer.filter(mc_uuid=uuid).first()


async def execute_list() -> str:
    """``\\rsvp list`` — everyone's active RSVP for the announced anni,
    split into HARD/SOFT groups."""
    event = await get_active_event()
    url = _dashboard_url()
    if event is None:
        return (
            f"No anni is currently announced. Watch the dashboard: {url}"
        )
    rsvps = await (
        Rsvp.filter(event=event, revoked_at__isnull=True)
        .select_related("player")
        .order_by("player__mc_username")
    )
    hard = [r.player.mc_username for r in rsvps if r.notice is AttendanceNotice.RSVP_HARD]
    soft = [r.player.mc_username for r in rsvps if r.notice is AttendanceNotice.RSVP_SOFT]

    def _fmt(group: list[str]) -> str:
        if not group:
            return "_nobody yet_"
        return ", ".join(f"`{n}`" for n in group)

    stamp = event.stamp_epoch
    return (
        f"**RSVPs for the anni <t:{stamp}:R> (<t:{stamp}:F>):**\n"
        f"**Hard ({len(hard)}):** {_fmt(hard)}\n"
        f"**Soft ({len(soft)}):** {_fmt(soft)}"
    )


async def execute_check(username: str, state: AppState) -> str:
    """``\\rsvp check <username>`` — a public profile snapshot.

    Surfaces: membership tier, Core/Fill + declared roles, current online
    state (online-merge cache; "unknown" for API-disabled), the RSVP +
    board placement for the announced anni. Cache-first IGN resolution —
    never spends the OWN-token bucket.
    """
    name = (username or "").strip()
    url = _dashboard_url()
    if not name:
        return "Specify an in-game name: `\\rsvp check <username>`."
    player = await _resolve_player_by_ign(name, state)
    if player is None:
        return (
            f"I don't know `{name}` — they haven't logged into the "
            f"dashboard, RSVP'd, or appeared on the board yet."
        )

    lines: list[str] = []
    if player.wynn_username and player.wynn_username != player.mc_username:
        lines.append(
            f"**`{player.mc_username}`** (in-game: `{player.wynn_username}`)"
        )
    else:
        lines.append(f"**`{player.mc_username}`**")
    lines.append(f"Membership: **{tier_label(player.membership_tier)}**")

    caps = await RoleCapability.filter(player=player).all()
    eligibility = "Core" if capability_domain.is_core(len(caps)) else "Fill"
    if caps:
        roles_listed = ", ".join(
            role_guidance(c.role).title
            for c in sorted(caps, key=lambda c: c.role.value)
        )
        lines.append(f"Eligibility: **{eligibility}** — {roles_listed}")
    else:
        lines.append(f"Eligibility: **{eligibility}** — no capabilities declared")

    # Online state — mirrors web/routers/user._build_specific.
    online = state.is_online(player.mc_uuid)
    if online is None:
        if identity_domain.is_api_disabled(player.last_online):
            lines.append("Online: _unknown — Wynncraft API disabled_")
        else:
            lines.append("Online: offline")
    elif online.queued:
        lines.append("Online: connecting (in queue)")
    elif online.server:
        lines.append(f"Online: yes, on `{online.server}`")
    else:
        lines.append("Online: yes")

    event = await get_active_event()
    if event is None:
        lines.append("RSVP: _no anni currently announced_")
    else:
        rsvp = await Rsvp.filter(
            event=event, player=player, revoked_at__isnull=True,
        ).first()
        if rsvp is None:
            lines.append("RSVP: _none for the current anni_")
        else:
            label = _notice_label(rsvp.notice)
            ts = int(rsvp.updated_at.timestamp())
            lines.append(f"RSVP: **{label}** (set <t:{ts}:R>)")
        placement = await (
            BoardPlacement.filter(event=event, player=player)
            .select_related("party")
            .first()
        )
        if placement is not None:
            if placement.party is not None:
                lines.append(f"Board: Party {placement.party.ordinal}")
            elif placement.bucket is not None:
                bucket_label = BUCKET_LABEL.get(
                    placement.bucket, placement.bucket.value
                )
                lines.append(f"Board: {bucket_label}")

    lines.append(f"Dashboard: {url}")
    return "\n".join(lines)


def _appstate() -> AppState:
    """Read the shared :class:`AppState` from the FastAPI singleton.

    Lazy import to dodge the cog↔main circular at module import time
    (``main.py`` only imports cogs at fishbot-start, by which point
    ``main.app`` is already constructed by :func:`create_app`).
    """
    from main import app
    return app.state.appstate


class RsvpCog(commands.Cog):
    """The discord.py shim around :func:`execute_rsvp`.

    Exposed as a *hybrid* group so both ``/rsvp …`` (slash) and ``\\rsvp …``
    (prefix, matching the fishbot convention) reach the same handler.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="rsvp", description="Manage your anni RSVP.")
    async def rsvp_group(self, ctx: commands.Context) -> None:
        # Bare ``\rsvp`` / ``/rsvp`` with no subcommand — show what's available.
        if ctx.invoked_subcommand is None:
            await ctx.reply(
                "Use `\\rsvp hard` / `soft` / `revoke` / `status` / `list` / "
                "`check <username>` (or the `/rsvp …` slash form).",
                ephemeral=True,
            )

    @rsvp_group.command(name="hard", description="Commit to attending (hard RSVP).")
    async def hard(self, ctx: commands.Context) -> None:
        await self._handle(ctx, "hard")

    @rsvp_group.command(name="soft", description="Tentative — might attend (soft RSVP).")
    async def soft(self, ctx: commands.Context) -> None:
        await self._handle(ctx, "soft")

    @rsvp_group.command(name="revoke", description="Withdraw your current RSVP.")
    async def revoke(self, ctx: commands.Context) -> None:
        await self._handle(ctx, "revoke")

    @rsvp_group.command(name="status", description="Show your RSVP + a dashboard link.")
    async def status(self, ctx: commands.Context) -> None:
        await self._handle(ctx, "status")

    @rsvp_group.command(
        name="list",
        description="Public: who has RSVP'd (hard vs soft) for the announced anni.",
    )
    async def list_(self, ctx: commands.Context) -> None:
        await self._handle_public(ctx, "list", execute_list)

    @rsvp_group.command(
        name="check",
        description="Public: full status readout of a player by in-game name.",
    )
    async def check(self, ctx: commands.Context, *, username: str) -> None:
        # ``*`` keyword-only consumes the rest of the prefix message into
        # ``username`` so ``\rsvp check Some Player`` works; for the slash
        # form it becomes a normal ``username:`` parameter.
        await self._handle_public(ctx, "check", execute_check, username, _appstate())

    async def _handle(self, ctx: commands.Context, action: Action) -> None:
        # Defer so a slow dazebot lookup doesn't time the interaction out in
        # front of the user. For prefix invocations Context.defer is a no-op
        # (no interaction to ack); ephemeral is silently ignored there too.
        await ctx.defer(ephemeral=True)
        try:
            outcome = await execute_rsvp(ctx.author.id, action)
        except Exception:
            logger.exception("/rsvp %s failed unexpectedly", action)
            await ctx.reply(
                "Something went wrong — staff have been notified.",
                ephemeral=True,
            )
            return

        await ctx.reply(outcome.private_message, ephemeral=True)

        if outcome.public_message:
            await _post_public(self.bot, outcome.public_message)

    async def _handle_public(
        self,
        ctx: commands.Context,
        action: str,
        fn,
        *args,
    ) -> None:
        """Shim for the read-only public subcommands (``list``/``check``).

        Defers non-ephemerally so a slow DB query can't time the slash
        interaction out, then replies in-channel. A handler crash is
        caught and turned into a friendly line — never an interaction
        timeout traceback in front of the user.
        """
        await ctx.defer()
        try:
            message = await fn(*args)
        except Exception:
            logger.exception("/rsvp %s failed unexpectedly", action)
            await ctx.reply("Something went wrong — staff have been notified.")
            return
        await ctx.reply(message)


async def _post_public(bot: commands.Bot, content: str) -> None:
    """Send the public confirmation line to ``RSVP_CHANNEL_ID`` (best-effort).

    A missing or unreachable channel is a config issue, not a user-visible
    failure: the ephemeral confirmation already landed, so we just log and
    move on. Mirrors the rest of fishbot's "Discord side is optional"
    posture (see :mod:`app.bot.client` for the FISHBOT_TOKEN gate).
    """
    channel_id = get_settings().rsvp_channel_id
    if not channel_id:
        logger.debug("RSVP_CHANNEL_ID unset — skipping public confirmation.")
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning("RSVP_CHANNEL_ID=%s not visible to fishbot", channel_id)
        return
    try:
        await channel.send(content)
    except discord.DiscordException:
        logger.exception("failed to post public RSVP confirmation")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RsvpCog(bot))
