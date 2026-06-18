"""UUID-keyed RSVP entrypoint — vetsmod's in-game ``/wv anni rsvp`` lands here.

Sibling of :func:`app.bot.cogs.rsvp.execute_rsvp` but keyed on a Minecraft
UUID supplied by the authenticated WS session in temporary-server rather than
a Discord snowflake. Skips the dazebot identity round-trip (the UUID is
already trusted) and never touches Discord directly; the optional ``bot``
parameter is threaded through so the public confirmation line still lands in
``RSVP_CHANNEL_ID`` exactly like the Discord cog produces it.

Reuses the same downstream helpers as ``_do_set`` / ``_do_revoke`` so the
Rsvp row, the auto-placement, the board-snapshot broadcast, and the public
post stay byte-identical between the two surfaces.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from app.bot.cogs.rsvp import (
    RsvpOutcome,
    _auto_place_after_rsvp,
    _broadcast_board_snapshot,
    _notice_label,
    _post_public,
)
from app.constants import AttendanceNotice
from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer
from app.domain import buckets as buckets_domain
from app.domain import rsvp as rsvp_domain
from app.services import hot_window

if TYPE_CHECKING:
    from discord.ext import commands  # noqa: F401 — type-only import


logger = logging.getLogger("anni.domain.rsvp_by_uuid")

Notice = Literal["hard", "soft", "revoke"]


class UuidRsvpError(Exception):
    """Validation/conflict error surfaced as an HTTP 4xx by the endpoint."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def execute_uuid_rsvp(
    bot: "commands.Bot | None",
    actor_mc_uuid: str,
    notice: Notice,
) -> RsvpOutcome:
    """Apply an in-game RSVP for ``actor_mc_uuid``.

    Mirrors the cog's ``_do_set`` / ``_do_revoke`` semantics including the
    T-90 cutoff (refused) and the auto-placement / board-snapshot broadcast
    chain. The optional ``bot`` is forwarded to :func:`_post_public` for the
    same Discord confirmation line; missing-bot or missing-channel is a
    silent no-op (matches the rest of fishbot's optional-Discord posture).

    Raises :class:`UuidRsvpError` for the four-ish refusable cases so the
    HTTP layer can map them to 4xx with a useful ``detail``.
    """
    event = await get_active_event()
    if event is None:
        raise UuidRsvpError(404, "no active anni event")

    player, _created = await AnniPlayer.get_or_create(
        mc_uuid=actor_mc_uuid,
        defaults={
            # WS side only has the UUID; mc_username gets hydrated by the
            # next auto-promoter / presence cycle. Same uuid[:8] fallback
            # auto_promoter uses for online-but-unknown players.
            "mc_username": actor_mc_uuid[:8],
            "is_placeholder": True,
        },
    )
    if _created:
        logger.info(
            "rsvp_by_uuid: created placeholder AnniPlayer for %s",
            actor_mc_uuid,
        )

    if notice == "revoke":
        outcome = await _do_revoke_uuid(player, event)
    else:
        if hot_window.is_rsvp_closed(event):
            raise UuidRsvpError(
                409, "RSVP is closed (within 90 min of anni)"
            )
        att_notice = (
            AttendanceNotice.RSVP_HARD if notice == "hard"
            else AttendanceNotice.RSVP_SOFT
        )
        outcome = await _do_set_uuid(player, event, att_notice)

    if bot is not None and outcome.public_message:
        await _post_public(bot, outcome.public_message)

    return outcome


async def _do_set_uuid(
    player: AnniPlayer,
    event,
    notice: AttendanceNotice,
) -> RsvpOutcome:
    """In-game ``/wv anni rsvp hard|soft`` — sibling of the cog's ``_do_set``.

    Same auto-place + broadcast chain. The public message format matches
    the cog's exactly (CLAUDE.md "anni timing must localise per viewer" —
    Discord timestamp tags only).
    """
    await rsvp_domain.set_rsvp(player, event, notice)
    inserted = await _auto_place_after_rsvp(player, event)
    if inserted:
        await _broadcast_board_snapshot(event)
    label = _notice_label(notice)
    username = player.mc_username or player.mc_uuid
    public = (
        f"`{username}` has **{label}** RSVP'd for the anni "
        f"<t:{event.stamp_epoch}:R> (<t:{event.stamp_epoch}:F>)."
    )
    private = f"RSVP recorded: {label}."
    return RsvpOutcome(private_message=private, public_message=public)


async def _do_revoke_uuid(player: AnniPlayer, event) -> RsvpOutcome:
    """In-game revoke — sibling of the cog's ``_do_revoke``.

    Silent in the public channel when there was no active RSVP to withdraw
    (matches the Discord side's "be friendly and don't spam" branch).
    """
    prior = await rsvp_domain.revoke(player, event)
    if prior is None:
        return RsvpOutcome(private_message="No active RSVP to withdraw.")
    await buckets_domain.demote_on_revoke(event, player)
    await _broadcast_board_snapshot(event)
    username = player.mc_username or player.mc_uuid
    return RsvpOutcome(
        private_message=(
            f"Your {_notice_label(prior.notice)} RSVP has been withdrawn."
        ),
        public_message=f"`{username}` withdrew their RSVP.",
    )
