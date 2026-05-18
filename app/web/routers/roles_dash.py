"""App3 — the roles dashboard (``GET /staff/roles``).

Everyone's declared capabilities in one staff-only read view: per player, the
core roles they can fill, each with weapons + confidence/build-quality meters +
lifetime WIN ``success_count``, plus their membership tier and Core/Fill
standing. It is the organiser's "who can do what" reference when balancing
parties on the board; capability *editing* stays the user's own ``/me`` surface
(low-trust model — staff don't impersonate users; they reset a stuck password
via the staff hub if someone is locked out).

Read-only + staff-gated; reuses the same chip/level macros as the user
dashboard so the colour-blind channels (glyph/label) are identical here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.constants import MEMBERSHIP_PRIORITY
from app.db.models import AnniPlayer
from app.domain import capability as cap_domain
from app.domain import regions as regions_domain
from app.domain.colourblind import role_chip
from app.domain.membership import label as tier_label
from app.domain.roles import guidance
from app.web import auth
from app.web.board_view import avatar
from app.web.deps import render

logger = logging.getLogger("anni.web.roles")
router = APIRouter()


@router.get("/staff/roles")
async def roles_dashboard(request: Request):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)

    players = (
        await AnniPlayer.all()
        .prefetch_related("capabilities__weapons")
        .order_by("mc_username")
    )
    rows: list[dict] = []
    core_count = 0
    for p in players:
        caps = sorted(p.capabilities, key=lambda c: c.role.value)
        is_core = cap_domain.is_core(len(caps))
        core_count += 1 if is_core else 0
        rows.append(
            {
                "uuid": p.mc_uuid,
                "name": p.mc_username,
                "wynn_username": p.wynn_username,
                "desynced": bool(
                    p.wynn_username and p.wynn_username != p.mc_username
                ),
                "avatar": avatar(p.mc_uuid, 32),
                "tier": p.membership_tier,
                "tier_label": tier_label(p.membership_tier),
                "tier_rank": MEMBERSHIP_PRIORITY.get(p.membership_tier, 9),
                "is_core": is_core,
                "regions": regions_domain.labelled(p.preferred_regions),
                "capabilities": [
                    {
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
        )
    # Highest-priority tier first, then Core before Fill, then name — the
    # order an organiser scans when filling a party.
    rows.sort(key=lambda r: (r["tier_rank"], not r["is_core"],
                             r["name"].lower()))
    logger.debug("roles dashboard: %d players, %d core", len(rows), core_count)
    return render(request, "staff/roles.html", rows=rows,
                  total=len(rows), core_count=core_count)
