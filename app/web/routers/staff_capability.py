"""Staff-side edit/delete for user role capabilities.

Mirrors the user's ``/me/capability/{id}`` edit + delete + weapons-autocomplete
under ``/staff/roles/capability/{id}``, so an organiser can correct a stale
declaration without waiting for the user. Reuses the user-side helpers
(``_parse_conf`` / ``_write_weapons`` from ``capability.py``) so weapon
validation, the cap on weapons per capability, and the success-count
write-protect stay identical between the two surfaces. After a mutation the
handler re-renders the *single* roles-dashboard row (``staff/_roles_row.html``)
so an HTMX ``outerHTML`` swap on ``#roles-row-{uuid}`` updates in place — no
full page reload, and the modal mount is cleared by the page-level JS once the
row finishes swapping in.

Staff-gated by ``auth.is_staff``; this is the surface that consciously *does*
let staff act on behalf of a user, so the low-trust posture is enforced at
the routes (not by the underlying capability domain).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.constants import MAX_WEAPONS_PER_CAPABILITY, Reliability
from app.db.models import RoleCapability
from app.domain import reliability
from app.domain.roles import guidance
from app.web import auth
from app.web.deps import render
from app.web.routers.capability import _parse_conf, _write_weapons
from app.web.routers.roles_dash import _row_response as roles_row_response
from app.web.ws.board_hub import maybe_broadcast_for

logger = logging.getLogger("anni.web.staff_capability")
router = APIRouter()


async def _row_response(
    request: Request, player_uuid: str
) -> HTMLResponse:
    """Re-render the swapped row. Delegates to the roles dashboard's renderer
    so the row keeps *all* its state after a capability edit — notably the
    "Delete profile" affordance, which a player who just lost their last
    capability may have gained."""
    return await roles_row_response(request, player_uuid)


def _render_modal(
    request: Request,
    cap: RoleCapability,
    *,
    modal_error: str | None = None,
) -> HTMLResponse:
    """Re-render the staff edit modal (used on initial open + on save errors).

    On error, HTMX is asked (via the response headers in
    :func:`staff_update_capability`) to retarget the modal mount instead of
    the row, so the error stays visible and the form keeps its state for the
    staff member to fix.
    """
    return render(
        request,
        "user/_capability_modal.html",
        mode="edit",
        cap={
            "id": str(cap.id),
            "role": cap.role,
            "confidence": cap.confidence,
            "success_count": cap.success_count,
            "weapons": ", ".join(w.weapon_name for w in cap.weapons),
        },
        role=cap.role,
        guidance=guidance(cap.role),
        available_roles=[cap.role],
        max_weapons=MAX_WEAPONS_PER_CAPABILITY,
        staff_reliability={
            "current": reliability.of(cap, cap.player.setbacks).value,
            "override": (
                cap.reliability_override.value if cap.reliability_override else None
            ),
            "set_at": cap.reliability_set_at,
        },
        form_action=f"/staff/roles/capability/{cap.id}",
        form_target=f"#roles-row-{cap.player.mc_uuid}",
        weapon_search_url="/staff/roles/capability/weapons",
        modal_error=modal_error,
    )


def _apply_reliability_override(cap: RoleCapability, choice: str) -> None:
    """``keep`` (the form default, so re-saving never re-bases), ``auto``
    (back to derived from the whole record), or a tier to restart at now —
    snapshotting the win count so only later wins move it."""
    choice = (choice or "").strip().lower()
    if choice == "auto":
        cap.reliability_override = None
        cap.reliability_set_at = None
        cap.reliability_set_wins = 0
        return
    try:
        tier = Reliability(choice)
    except ValueError:
        return  # "keep", or anything unrecognised
    cap.reliability_override = tier
    cap.reliability_set_at = datetime.now(timezone.utc)
    cap.reliability_set_wins = cap.success_count
    logger.info(
        "staff set reliability: %s -> %s = %s",
        cap.player.mc_username, cap.role.value, tier.value,
    )


@router.get("/staff/roles/capability/{cap_id}/edit", include_in_schema=False)
async def staff_edit_modal(request: Request, cap_id: str):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    cap = (
        await RoleCapability.filter(id=cap_id)
        .prefetch_related("weapons", "player", "player__setbacks")
        .first()
    )
    if cap is None:
        return RedirectResponse("/staff/roles", status_code=303)
    return _render_modal(request, cap)


@router.post("/staff/roles/capability/{cap_id}", include_in_schema=False)
async def staff_update_capability(
    request: Request,
    cap_id: str,
    confidence: str = Form("moderate"),
    weapons: str = Form(""),
    reliability_override: str = Form("keep"),
):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    cap = (
        await RoleCapability.filter(id=cap_id)
        .prefetch_related("player", "weapons", "player__setbacks")
        .first()
    )
    if cap is None:
        return RedirectResponse("/staff/roles", status_code=303)
    cap.confidence = _parse_conf(confidence, cap.confidence)
    _apply_reliability_override(cap, reliability_override)
    await cap.save(update_fields=[
        "confidence", "reliability_override", "reliability_set_at",
        "reliability_set_wins", "updated_at",
    ])
    # Staff filling in someone's capability is enough signal to upgrade
    # them out of the auto-promoter "Unregistered" stub-card state.
    from app.domain.identity import mark_registered
    await mark_registered(cap.player)
    ok, err, _flagged = await _write_weapons(request, cap, weapons)
    if not ok:
        # Weapon validation rejected the input — keep the modal open with
        # the error visible. The HX-Retarget/-Reswap headers override the
        # form's row-targeted swap for this single response.
        await cap.fetch_related("weapons")
        resp = _render_modal(request, cap, modal_error=err)
        resp.headers["HX-Retarget"] = "#modal-mount"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
    logger.info(
        "staff edited capability: %s -> %s",
        cap.player.mc_username, cap.role.value,
    )
    await maybe_broadcast_for(cap.player.mc_uuid)
    return await _row_response(request, cap.player.mc_uuid)


@router.post("/staff/roles/capability/{cap_id}/delete", include_in_schema=False)
async def staff_delete_capability(request: Request, cap_id: str):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    cap = (
        await RoleCapability.filter(id=cap_id)
        .prefetch_related("player")
        .first()
    )
    if cap is None:
        return RedirectResponse("/staff/roles", status_code=303)
    player_uuid = cap.player.mc_uuid
    player_name = cap.player.mc_username
    role_value = cap.role.value
    await cap.delete()
    logger.info(
        "staff deleted capability: %s / %s", player_name, role_value
    )
    await maybe_broadcast_for(player_uuid)
    return await _row_response(request, player_uuid)


@router.get("/staff/roles/capability/weapons", include_in_schema=False)
async def staff_weapons_autocomplete(request: Request, q: str = ""):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    needle = q.strip().lower()
    catalog = request.app.state.appstate.weapons_by_name
    matches: list[dict] = []
    if needle:
        for name_lower, subtype in catalog.items():
            if needle in name_lower:
                matches.append({"name": name_lower, "subtype": subtype})
            if len(matches) >= 12:
                break
    return render(request, "user/_weapon_options.html", matches=matches)
