"""guild_refresh_poller — keep ``anni_player.guild`` / ``membership_tier`` fresh.

Background: those two fields are written only at dashboard login and the staff
"Add Player by IGN" walk-in flow. In steady state a row drifts indefinitely —
e.g. a member who rejoins Returners but never reopens the dashboard stays
``guild=NULL, tier=community`` until somebody touches them by hand. The
Piplup bug that motivated this poller; see ``.claude/`` design notes.

Two tracks per tick (both reuse :func:`app.domain.membership.resolve` so we
don't reinvent the multi-signal classification that lives at login):

* **Track A — free reconciliation.** Walk every ``anni_player`` row and feed
  the cached signals (Returners roster from ``state.roster_by_uuid``;
  live vetsmod tier from ``state.online_by_uuid``) into ``resolve()``. Zero
  outbound HTTP, runs every tick. Heals the Piplup case immediately — when
  he reappears in the cached roster Track A sets ``guild='Returners'`` /
  ``tier=MEMBER`` next tick.

* **Track B — capped per-tick reclassification.** Two trigger paths:
  *drift* (row claimed ``guild='Returners'`` but is no longer in the cached
  roster — needs to be reclassified, but we can't speculate guild_name from
  cache alone), and *floor staleness* (any row whose ``updated_at`` is older
  than ``guild_refresh_floor_seconds`` regardless of window state). Up to
  ``guild_refresh_call_cap_per_tick`` rows per tick get a dazebot
  ``/api/internal/check-snapshot/{uuid}`` lookup (authoritative for
  HONOURARY/WAITLIST + blocklisted + Returners) and — only when the snapshot
  says they're not in Returners — one WAPI ``/v3/player/{uuid}`` lookup for
  ``guild_name`` display freshness. Bucket-aware via :mod:`app.services.wapi`'s
  PRIO_LOW queue; the cap keeps the burst inside fishbot's own quota even on
  the busiest tick.

Cadence ramps via :func:`hot_window.is_currently_hot` exactly like
``presence_poller`` / ``online_merge``. Hot interval (60 s by default) is
half of the upstream WAPI guild cache's 120 s TTL — polling tighter would
just churn the same cached data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from tortoise.transactions import in_transaction

from app.constants import MembershipTier
from app.db.models import AnniPlayer
from app.domain import membership
from app.services import hot_window
from app.services.dazebot_client import CheckSnapshot, get_dazebot_client
from app.services.loop import poll_forever
from app.services.state import AppState
from app.services.wapi import PRIO_LOW, WapiError, get_wapi
from app.settings import Settings

logger = logging.getLogger("anni.guild_refresh")


def _track_a_compute(
    row: AnniPlayer,
    state: AppState,
    settings: Settings,
) -> tuple[str | None, MembershipTier]:
    """Return the (guild, tier) Track A would write for ``row``.

    Uses only in-process state — never any I/O. ``guild_name`` is NEVER
    *cleared* here: a row marked Returners that's absent from the cached
    roster is left as-is so Track B can resolve it authoritatively
    (clearing speculatively would mis-classify a row whose tempserver
    roster is stale for one tick).
    """
    in_roster = row.mc_uuid in state.roster_by_uuid
    online = state.online_by_uuid.get(row.mc_uuid)
    # Live vetsmod tier signal — only useful when it asserts WAITLIST /
    # HONOURARY (the WAPI-blind classifications). Other values either
    # match the roster check or aren't tiering signals.
    online_tier_hint: str | None = None
    if online is not None and online.tier in ("waitlist", "honourary"):
        online_tier_hint = online.tier

    new_guild = settings.returners_guild_name if in_roster else row.guild
    new_tier = membership.resolve(
        in_returners_roster=in_roster,
        dazebot_tier=online_tier_hint,
        guild_name=new_guild,
        guild_tag=None,
        ally_tags=settings.ally_guild_tag_set,
        returners_guild_name=settings.returners_guild_name,
    )
    return new_guild, new_tier


async def _track_b_compute(
    row: AnniPlayer,
    snap: CheckSnapshot,
    settings: Settings,
) -> tuple[str | None, MembershipTier]:
    """Reclassify ``row`` against a fresh dazebot snapshot.

    Authoritative on: ``in_returners_guild`` (dazebot just re-fetched the
    MC guild server-side), ``discord_tier`` (HONOURARY / WAITLIST). For
    non-Returners rows we spend ONE PRIO_LOW WAPI ``/v3/player/{uuid}`` to
    refresh ``guild_name`` so the display doesn't show the wrong guild
    forever; WAPI failure is non-fatal — we just leave ``guild`` cleared.
    """
    new_guild: str | None
    new_tag: str | None = None

    if snap.in_returners_guild:
        new_guild = settings.returners_guild_name
    else:
        new_guild = None
        try:
            profile = await get_wapi().get_json(
                f"player/{row.mc_uuid}", priority=PRIO_LOW,
            )
        except WapiError as exc:
            logger.info(
                "wapi player/%s skipped (%s) — leaving guild_name unset",
                row.mc_uuid, exc,
            )
            profile = None
        except Exception:  # noqa: BLE001 - resilience: bad WAPI must not kill the tick
            logger.warning(
                "wapi player/%s errored — leaving guild_name unset",
                row.mc_uuid, exc_info=True,
            )
            profile = None
        if isinstance(profile, dict):
            g = profile.get("guild")
            if isinstance(g, dict):
                name = g.get("name")
                prefix = g.get("prefix")
                new_guild = str(name) if isinstance(name, str) and name else None
                new_tag = str(prefix) if isinstance(prefix, str) and prefix else None

    new_tier = membership.resolve(
        in_returners_roster=snap.in_returners_guild,
        dazebot_tier=snap.discord_tier,
        guild_name=new_guild,
        guild_tag=new_tag,
        ally_tags=settings.ally_guild_tag_set,
        returners_guild_name=settings.returners_guild_name,
    )
    return new_guild, new_tier


async def _tick(state: AppState, settings: Settings) -> None:
    # Bail on empty roster — if online_merge has never succeeded (e.g.
    # boot, or tempserver wedged from the start) every row would look like
    # drift and we'd queue a mass Track B sweep against stale truth.
    if not state.roster_by_uuid:
        logger.debug("guild_refresh: roster empty, skipping tick")
        return

    now = datetime.now(timezone.utc)
    floor_cutoff = now - timedelta(seconds=settings.guild_refresh_floor_seconds)
    returners_name = settings.returners_guild_name

    rows = await AnniPlayer.all()
    track_a_updates: list[AnniPlayer] = []
    track_b_candidates: list[AnniPlayer] = []

    for row in rows:
        original_guild = row.guild
        original_tier = row.membership_tier
        original_updated_at = row.updated_at

        new_guild, new_tier = _track_a_compute(row, state, settings)
        changed_a = new_guild != original_guild or new_tier != original_tier
        if changed_a:
            row.guild = new_guild
            row.membership_tier = new_tier
            track_a_updates.append(row)

        # Drift: row's persisted guild claims Returners but cached roster
        # disagrees. Cache could be stale for one tick — Track B asks dazebot
        # (which itself re-fetches MC guild live) to confirm.
        drift = original_guild == returners_name and row.mc_uuid not in state.roster_by_uuid
        # Floor: row hasn't been touched in too long. Only queue if Track A
        # didn't already refresh updated_at this tick.
        floor_stale = (
            not changed_a
            and original_updated_at is not None
            and original_updated_at < floor_cutoff
        )
        if drift or floor_stale:
            track_b_candidates.append(row)

    # Persist Track A — per-row save with explicit update_fields matches the
    # rest of the codebase (Tortoise's auto_now needs to be listed to fire).
    if track_a_updates:
        async with in_transaction():
            for row in track_a_updates:
                await row.save(
                    update_fields=["guild", "membership_tier", "updated_at"],
                )
        logger.debug("guild_refresh track-a: %d rows updated", len(track_a_updates))

    cap = max(0, settings.guild_refresh_call_cap_per_tick)
    track_b = track_b_candidates[:cap]
    if not track_b:
        logger.debug(
            "guild_refresh tick: %d rows scanned, track-a=%d, track-b skipped",
            len(rows), len(track_a_updates),
        )
        return

    daze = get_dazebot_client()
    track_b_updates: list[AnniPlayer] = []
    for row in track_b:
        snap = await daze.check_snapshot(row.mc_uuid)
        if snap is None:
            continue  # transport/auth fail — try again next floor sweep
        new_guild, new_tier = await _track_b_compute(row, snap, settings)
        if new_guild != row.guild or new_tier != row.membership_tier:
            row.guild = new_guild
            row.membership_tier = new_tier
            track_b_updates.append(row)

    if track_b_updates:
        async with in_transaction():
            for row in track_b_updates:
                await row.save(
                    update_fields=["guild", "membership_tier", "updated_at"],
                )

    logger.debug(
        "guild_refresh tick: %d rows scanned, track-a=%d, track-b=%d/%d "
        "(capped at %d)",
        len(rows), len(track_a_updates), len(track_b_updates), len(track_b), cap,
    )


async def run(state: AppState, settings: Settings) -> None:
    def _interval() -> float:
        return float(
            settings.guild_refresh_hot_seconds
            if hot_window.is_currently_hot()
            else settings.guild_refresh_seconds
        )

    await poll_forever(
        "guild_refresh", _interval, lambda: _tick(state, settings),
    )
