"""App3 — **Phase-1 minimal staff surface only.**

The full staff status page + organizer drag-drop board + roles dashboard are
Phase 2. Phase 1 ships just the auth plumbing the low-trust model needs and
the spec's "staff tools to reset passwords": staff login, admin-gated staff-
password rotation, and clearing a user's stuck password. Everything heavier
(``/staff/board``, ``/staff/roles``, the WS hub) arrives in Phase 2.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.db.models import AnniPlayer
from app.web import auth
from app.web.deps import clear_session, render, write_session

logger = logging.getLogger("anni.web.staff")
router = APIRouter()


@router.get("/staff")
async def staff_home(request: Request):
    """Login card when signed-out; the minimal Phase-1 tools when staff."""
    if not auth.is_staff(request):
        return render(request, "staff/login.html")
    players = await AnniPlayer.all().order_by("mc_username").limit(500)
    return render(
        request,
        "staff/home.html",
        users=[
            {
                "mc_uuid": p.mc_uuid,
                "name": p.mc_username,
                "has_password": bool(p.password_hash),
            }
            for p in players
        ],
    )


@router.post("/staff/login", include_in_schema=False)
async def staff_login(request: Request, password: str = Form("")):
    if not await auth.check_staff_password(password):
        logger.info("staff login refused (wrong password)")
        return render(request, "staff/login.html",
                      error="Wrong staff password.")
    logger.info("staff signed in")
    resp = RedirectResponse("/staff", status_code=303)
    write_session(resp, {"kind": "staff"})
    return resp


@router.get("/staff/logout", include_in_schema=False)
async def staff_logout():
    resp = RedirectResponse("/", status_code=303)
    clear_session(resp)
    return resp


@router.post("/staff/users/{mc_uuid}/clear-password", include_in_schema=False)
async def clear_user_password(request: Request, mc_uuid: str):
    """Staff-only: drop a user's stuck password (spec's reset tool)."""
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    cleared = await auth.clear_user_password(mc_uuid)
    logger.info("staff cleared password for %s (found=%s)", mc_uuid, cleared)
    return RedirectResponse("/staff", status_code=303)


@router.post("/staff/rotate-password", include_in_schema=False)
async def rotate_staff_password(
    request: Request,
    admin_password: str = Form(""),
    new_password: str = Form(""),
):
    """Admin-gated staff-password rotation (low-trust ADMIN_PASSWORD)."""
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    ok = await auth.rotate_staff_password(admin_password, new_password)
    logger.info("staff password rotation %s", "succeeded" if ok else "refused")
    players = await AnniPlayer.all().order_by("mc_username").limit(500)
    return render(
        request,
        "staff/home.html",
        users=[
            {"mc_uuid": p.mc_uuid, "name": p.mc_username,
             "has_password": bool(p.password_hash)}
            for p in players
        ],
        rotate_msg="Staff password rotated." if ok
        else "Rotation refused (check the admin password).",
    )
