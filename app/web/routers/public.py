"""Public (anonymous-safe) routes: login, logout, overview, health, CB toggle.

Phase 1 wires the real IGN(+optional-password) login (``app.web.auth``) and
enriches the overview from the stamp/staff caches. No destructive action is
reachable here — the auth model is intentionally low-trust (see
``.claude/integration.md``).
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.db.lifecycle import get_active_event
from app.db.models import Party
from app.web import auth
from app.web.deps import (
    clear_session,
    colourblind,
    render,
    set_colourblind,
    write_session,
)

logger = logging.getLogger("anni.web.public")
router = APIRouter()


def _state(request: Request):
    """The shared AppState (always present — created in ``main.create_app``)."""
    return request.app.state.appstate


@router.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Liveness probe used by the deploy verification + Traefik checks."""
    return JSONResponse({"status": "ok"})


@router.get("/")
async def login_screen(request: Request):
    """Participant landing: sign-in card + today's anni status card."""
    if (await auth.current_user(request)) is not None:
        return RedirectResponse("/me", status_code=303)
    event = await get_active_event()
    return render(request, "public/login.html", event=event)


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
):
    """Resolve IGN -> UUID, upsert the player, apply the optional-password
    rule, and start a signed-cookie session. Re-renders the card with a
    friendly error otherwise (never a 4xx/5xx for a bad IGN/password)."""
    logger.debug("POST /login for IGN %r (password %s)",
                 username, "given" if password else "blank")
    outcome = await auth.login_user(username, password, _state(request))
    if not outcome.ok:
        event = await get_active_event()
        return render(
            request, "public/login.html", event=event, error=outcome.error,
            prefill=username,
        )
    resp = RedirectResponse("/me", status_code=303)
    write_session(resp, {"kind": "user", "mc_uuid": outcome.player.mc_uuid,
                         "name": outcome.player.mc_username})
    return resp


@router.get("/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/", status_code=303)
    clear_session(resp)
    return resp


@router.get("/overview")
async def overview(request: Request):
    """Generic anni info for everyone — no per-user assignments. Countdown
    derives from the stamp poller, so it matches ``/v1/outbound/stamp``."""
    event = await get_active_event()
    parties: list[dict] = []
    if event is not None:
        rows = await Party.filter(event=event).select_related("host").order_by("ordinal")
        parties = [
            {
                "ordinal": p.ordinal,
                "host": p.host.mc_username if p.host else None,
                "world": p.world,
                "stage": p.stage,
            }
            for p in rows
        ]
    st = _state(request)
    return render(
        request,
        "public/overview.html",
        event=event,
        parties=parties,
        staff_online=len(st.online_staff),
        online_count=len(st.online_by_uuid),
        now=int(time.time()),
    )


@router.get("/toggle-cb", include_in_schema=False)
async def toggle_colourblind(request: Request):
    """Flip the per-user colourblind variant and bounce back where we came
    from. Present on every interface (spec hard requirement)."""
    target = request.query_params.get("next") or request.headers.get("referer") or "/"
    resp = RedirectResponse(target, status_code=303)
    set_colourblind(resp, not colourblind(request))
    return resp
