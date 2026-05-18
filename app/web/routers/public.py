"""Public (anonymous-safe) routes: login screen, overview, health, CB toggle.

Phase 0 ships the login *screen* and a placeholder overview so the app boots
and is visibly styled. The login POST + a populated overview land in Phase 1
(they need the identity resolver + the stamp poller).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.db.lifecycle import get_active_event
from app.web.deps import colourblind, render, set_colourblind

router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Liveness probe used by the deploy verification + Traefik checks."""
    return JSONResponse({"status": "ok"})


@router.get("/")
async def login_screen(request: Request):
    """The participant landing: a sign-in card + today's anni status card.
    (Concept-art page 1.)"""
    event = await get_active_event()
    return render(request, "public/login.html", event=event)


@router.get("/overview")
async def overview(request: Request):
    """Generic anni info for everyone — no per-user assignments. Filled in by
    the stamp poller in Phase 1; Phase 0 shows the announced/not-announced
    shell so the route exists and is styled."""
    event = await get_active_event()
    return render(request, "public/overview.html", event=event)


@router.get("/toggle-cb", include_in_schema=False)
async def toggle_colourblind(request: Request):
    """Flip the colourblind variant and bounce back where we came from.
    Available on every interface (spec hard requirement)."""
    target = request.query_params.get("next") or request.headers.get("referer") or "/"
    resp = RedirectResponse(target, status_code=303)
    set_colourblind(resp, not colourblind(request))
    return resp
