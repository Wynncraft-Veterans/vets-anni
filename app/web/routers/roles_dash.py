"""App3 — the roles dashboard (``GET /staff/roles``).

Everyone's declared capabilities in one staff-only read view: per player, the
core roles they can fill, each with weapons + confidence/build-quality meters +
lifetime WIN ``success_count``, plus their membership tier and Core/Fill
standing. It is the organiser's "who can do what" reference when balancing
parties on the board.

Per-row Edit / Delete buttons let staff correct a stale declaration without
waiting for the player to log in (e.g. someone listed a weapon they no longer
build for). The mutation routes live in ``staff_capability.py``; the row
partial here (``staff/_roles_row.html``) re-renders standalone after each
mutation so an HTMX swap on ``#roles-row-{uuid}`` updates a single row in
place. This is a deliberate widening of the low-trust posture from the
Phase-1 stance — staff *can* act on behalf of users for capability data, but
password resets still go through the staff hub's separate "Reset a user's
password" tool, and a user editing their own ``/me`` page remains the primary
authoring surface.

Staff-gated; reuses the same chip/level macros as the user dashboard so the
colour-blind channels (glyph/label) are identical here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.constants import MEMBERSHIP_PRIORITY, AttendanceNotice, MembershipTier
from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer, Rsvp
from app.domain import capability as cap_domain
from app.domain import regions as regions_domain
from app.domain.colourblind import role_chip
from app.domain.membership import label as tier_label
from app.domain.roles import capability_roles, guidance
from app.services.state import AppState
from app.web import auth
from app.web.board_view import avatar
from app.web.deps import render

logger = logging.getLogger("anni.web.roles")
router = APIRouter()


async def view_signals(
    state: AppState,
) -> tuple[set[str], dict[str, AttendanceNotice], bool]:
    """Per-uuid presence signals the dashboard surfaces.

    Returns ``(active_uuids, rsvp_by_uuid, has_event)``:

    * ``active_uuids`` — online OR RSVP'd; powers the "Active only" toggle and
      matches the green/yellow/blue/cyan/magenta org-board presence colours
      (everything except OFFLINE_GONE/red and UNKNOWN/grey).
    * ``rsvp_by_uuid`` — non-revoked RSVP notice keyed by player uuid; powers
      the ✓/✗ badge under each avatar.
    * ``has_event`` — ``False`` when no active event exists (RSVP is undefined,
      so the badge is suppressed entirely rather than showing a misleading ✗).

    Cheap: one set copy + one indexed RSVP query for the active event.
    """
    online_uuids: set[str] = set(state.online_by_uuid.keys())
    rsvp_by_uuid: dict[str, AttendanceNotice] = {}
    event = await get_active_event()
    if event is not None:
        rsvps = (
            await Rsvp.filter(event=event, revoked_at=None)
            .select_related("player")
        )
        rsvp_by_uuid = {r.player.mc_uuid: r.notice for r in rsvps}
    return online_uuids | set(rsvp_by_uuid.keys()), rsvp_by_uuid, event is not None


def row_for(
    player: AnniPlayer,
    *,
    active_uuids: set[str] | None = None,
    rsvp_by_uuid: dict[str, AttendanceNotice] | None = None,
    has_event: bool = False,
) -> dict:
    """Build the single-player row dict the roles dashboard renders.

    Assumes ``player.capabilities`` and their ``.weapons`` are prefetched
    (the listing endpoint does this in a single query; the staff-capability
    edit/delete handlers re-fetch one player with the same prefetch before
    re-rendering ``staff/_roles_row.html``).

    ``active_uuids``/``rsvp_by_uuid``/``has_event`` come from
    :func:`view_signals`. With no active event the RSVP badge is suppressed
    (``rsvp_state='na'``) so we never render a misleading ✗; when the caller
    omits these the row degrades to "no presence info" — the toggle won't
    keep it visible and the badge is hidden.
    """
    caps = sorted(player.capabilities, key=lambda c: c.role.value)
    is_core = cap_domain.is_core(len(caps))
    region_codes = regions_domain.parse(player.preferred_regions)
    wins_total = sum(c.success_count for c in caps)
    notice = rsvp_by_uuid.get(player.mc_uuid) if rsvp_by_uuid else None
    if not has_event:
        rsvp_state = "na"           # no event => hide the badge
    elif notice == AttendanceNotice.RSVP_HARD:
        rsvp_state = "hard"
    elif notice == AttendanceNotice.RSVP_SOFT:
        rsvp_state = "soft"
    else:
        rsvp_state = "none"         # event exists, player hasn't RSVP'd
    return {
        "uuid": player.mc_uuid,
        "name": player.mc_username,
        "wynn_username": player.wynn_username,
        "desynced": bool(
            player.wynn_username and player.wynn_username != player.mc_username
        ),
        "avatar": avatar(player.mc_uuid, 32),
        "tier": player.membership_tier,
        "tier_value": player.membership_tier.value,
        "tier_label": tier_label(player.membership_tier),
        "tier_rank": MEMBERSHIP_PRIORITY.get(player.membership_tier, 9),
        "is_core": is_core,
        "is_active": active_uuids is not None and player.mc_uuid in active_uuids,
        "rsvp_state": rsvp_state,
        "wins_total": wins_total,
        "region_codes_csv": ",".join(c.value for c in region_codes),
        "role_values_csv": ",".join(c.role.value for c in caps),
        "regions": regions_domain.labelled(player.preferred_regions),
        "capabilities": [
            {
                "id": str(c.id),
                "role": c.role,
                "role_label": guidance(c.role).title,
                "chip": role_chip(c.role),
                "confidence": c.confidence,
                "build_quality": c.build_quality,
                "success_count": c.success_count,
                "weapons": [
                    {"name": w.weapon_name, "subtype": w.weapon_subtype}
                    for w in c.weapons
                ],
            }
            for c in caps
        ],
    }


@router.get("/staff/roles")
async def roles_dashboard(request: Request):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)

    players = (
        await AnniPlayer.all()
        .prefetch_related("capabilities__weapons")
        .order_by("mc_username")
    )
    active, rsvp_by_uuid, has_event = await view_signals(
        request.app.state.appstate
    )
    rows = [row_for(p, active_uuids=active, rsvp_by_uuid=rsvp_by_uuid,
                    has_event=has_event) for p in players]
    core_count = sum(1 for r in rows if r["is_core"])
    # Highest-priority tier first, then Core before Fill, then name — the
    # order an organiser scans when filling a party.
    rows.sort(key=lambda r: (r["tier_rank"], not r["is_core"],
                             r["name"].lower()))

    tier_options = [
        {"value": t.value, "label": tier_label(t)}
        for t in sorted(MembershipTier, key=lambda t: MEMBERSHIP_PRIORITY[t])
    ]
    role_options = [
        {"value": role.value, "label": guidance(role).title}
        for role in capability_roles()
    ]
    region_options = [
        {"value": code.value, "label": label}
        for code, label in regions_domain.choices()
    ]
    logger.debug("roles dashboard: %d players, %d core", len(rows), core_count)
    return render(request, "staff/roles.html", rows=rows,
                  total=len(rows), core_count=core_count,
                  tier_options=tier_options, role_options=role_options,
                  region_options=region_options)
