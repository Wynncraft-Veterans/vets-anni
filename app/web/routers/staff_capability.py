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

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.constants import MAX_WEAPONS_PER_CAPABILITY
from app.db.models import AnniPlayer, RoleCapability
from app.domain.roles import guidance
from app.web import auth
from app.web.deps import render
from app.web.routers.capability import _parse_conf, _write_weapons
from app.web.routers.roles_dash import row_for, view_signals

logger = logging.getLogger("anni.web.staff_capability")
router = APIRouter()


async def _row_response(
    request: Request, player_uuid: str
) -> HTMLResponse:
    player = (
        await AnniPlayer.filter(mc_uuid=player_uuid)
        .prefetch_related("capabilities__weapons")
        .first()
    )
    if player is None:
        return RedirectResponse("/staff/roles", status_code=303)
    active, rsvp_by_uuid, has_event = await view_signals(
        request.app.state.appstate
    )
    return render(request, "staff/_roles_row.html",
                  r=row_for(player, active_uuids=active,
                            rsvp_by_uuid=rsvp_by_uuid, has_event=has_event))


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
            "build_quality": cap.build_quality,
            "success_count": cap.success_count,
            "weapons": ", ".join(w.weapon_name for w in cap.weapons),
        },
        role=cap.role,
        guidance=guidance(cap.role),
        available_roles=[cap.role],
        max_weapons=MAX_WEAPONS_PER_CAPABILITY,
        form_action=f"/staff/roles/capability/{cap.id}",
        form_target=f"#roles-row-{cap.player.mc_uuid}",
        weapon_search_url="/staff/roles/capability/weapons",
        modal_error=modal_error,
    )


@router.get("/staff/roles/capability/{cap_id}/edit", include_in_schema=False)
async def staff_edit_modal(request: Request, cap_id: str):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    cap = (
        await RoleCapability.filter(id=cap_id)
        .prefetch_related("weapons", "player")
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
    build_quality: str = Form("moderate"),
    weapons: str = Form(""),
):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    cap = (
        await RoleCapability.filter(id=cap_id)
        .prefetch_related("player", "weapons")
        .first()
    )
    if cap is None:
        return RedirectResponse("/staff/roles", status_code=303)
    cap.confidence = _parse_conf(confidence, cap.confidence)
    cap.build_quality = _parse_conf(build_quality, cap.build_quality)
    await cap.save(update_fields=["confidence", "build_quality", "updated_at"])
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
