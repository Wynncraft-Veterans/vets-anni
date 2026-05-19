"""App3 — the staff hub: status + organiser claim + the password tools.

Signed-out ⇒ the same low-trust staff-password login as Phase 1 (unchanged —
``staff/login.html``). Signed-in ⇒ the hub: the anni countdown/phase, an
organiser claim/release control, the online-staff snapshot, a party-formation
summary, links into the organizer board (``/staff/board``) and roles dashboard
(``/staff/roles``), and the Phase-1 password tools (reset a user's stuck
password; rotate the staff password behind the admin gate). Non-sensitive
status only — the heavy mutation surface is the board's WS/REST in
``organizer.py``.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer
from app.domain import buckets
from app.domain.schedule import phase_of
from app.settings import get_settings
from app.web import auth
from app.web.board_view import avatar
from app.web.deps import clear_session, render, write_session

logger = logging.getLogger("anni.web.staff")
router = APIRouter()


def _state(request: Request):
    return request.app.state.appstate


async def _hub_ctx(request: Request) -> dict:
    """View-model for the signed-in staff hub (status + tools)."""
    state = _state(request)
    settings = get_settings()
    event = await get_active_event()

    phase = parties = organizer = None
    if event is not None:
        phase = phase_of(
            event.stamp_epoch, max(0, settings.grace_hours) * 3600
        ).value
        parties = [
            {
                "ordinal": p.ordinal,
                "host": p.host.mc_username if p.host else None,
                "host_avatar": avatar(p.host.mc_uuid, 24) if p.host else None,
                "world": p.world,
                "stage": p.stage,
                "result": p.result.value,
            }
            for p in await buckets.parties_of(event)
        ]
        if event.organizer:
            organizer = {
                "uuid": event.organizer.mc_uuid,
                "name": event.organizer.mc_username,
                "avatar": avatar(event.organizer.mc_uuid, 24),
            }

    # "Staff online" stays the live online-only feed (a presence display);
    # the organiser candidate list is the FULL WAPI guild staff (offline too).
    online_staff = sorted(
        (
            {"uuid": v["uuid"], "name": v["username"], "rank": v["rank"]}
            for v in state.online_staff.values()
        ),
        key=lambda s: s["name"].lower(),
    )
    organizer_candidates = sorted(
        (
            {"uuid": v["uuid"], "name": v["username"], "rank": v["rank"]}
            for v in state.guild_staff.values()
        ),
        key=lambda s: s["name"].lower(),
    )
    players = await AnniPlayer.all().order_by("mc_username").limit(500)
    return {
        "event": event,
        "phase": phase,
        "parties": parties,
        "organizer": organizer,
        "online_staff": online_staff,
        "organizer_candidates": organizer_candidates,
        "now": int(time.time()),
        "users": [
            {"mc_uuid": p.mc_uuid, "name": p.mc_username,
             "has_password": bool(p.password_hash)}
            for p in players
        ],
    }


@router.get("/staff")
async def staff_home(request: Request):
    """Login card when signed-out; the status+tools hub when staff."""
    if not auth.is_staff(request):
        return render(request, "staff/login.html")
    return render(request, "staff/home.html", **await _hub_ctx(request))


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


@router.post("/staff/organizer", include_in_schema=False)
async def claim_organizer(request: Request, player_uuid: str = Form("")):
    """Claim (a chosen guild staffer) or release (blank) the lead-organiser
    slot. Same as the board's WS ``ORGANIZER_SET``; the no-JS path on the hub.
    A staffer with no :class:`AnniPlayer` yet is get-or-created by
    ``set_organizer`` from the cached guild-staff name."""
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    event = await get_active_event()
    if event is not None:
        uuid = player_uuid.strip() or None
        state = _state(request)
        cached = (
            state.guild_staff.get(uuid) or state.online_staff.get(uuid)
        ) if uuid else None
        await buckets.set_organizer(
            event, uuid, name=(cached or {}).get("username")
        )
        logger.info("organiser %s via staff hub", "released" if not uuid else uuid)
    return RedirectResponse("/staff", status_code=303)


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
    return render(
        request,
        "staff/home.html",
        rotate_msg="Staff password rotated." if ok
        else "Rotation refused (check the admin password).",
        **await _hub_ctx(request),
    )
