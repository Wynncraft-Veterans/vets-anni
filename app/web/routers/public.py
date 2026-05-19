"""Public (anonymous-safe) routes: login, logout, overview, health, CB toggle.

Phase 1 wires the real IGN(+optional-password) login (``app.web.auth``) and
enriches the overview from the stamp/staff caches. No destructive action is
reachable here — the auth model is intentionally low-trust (see
``.claude/integration.md``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.db.lifecycle import get_active_event
from app.db.models import Party
from app.web import auth
from app.web.routers.user import _avatar
from app.web.deps import (
    clear_session,
    colourblind,
    label_pref_on,
    pin_legend,
    render,
    set_colourblind,
    set_label_pref,
    set_pin,
    write_session,
)

logger = logging.getLogger("anni.web.public")
router = APIRouter()


def _state(request: Request):
    """The shared AppState (always present — created in ``main.create_app``)."""
    return request.app.state.appstate


def _organizer(event) -> dict | None:
    """``{name, avatar}`` for the event's lead organiser, or ``None``.
    Shared by the landing page + overview so the avatar pill is identical."""
    if event is None or not event.organizer:
        return None
    return {
        "name": event.organizer.mc_username,
        "avatar": _avatar(event.organizer.mc_uuid, 24),
    }


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
    return render(request, "public/login.html", event=event,
                  organizer=_organizer(event))


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
            request, "public/login.html", event=event,
            organizer=_organizer(event), error=outcome.error, prefill=username,
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
    """Generic anni status (no per-user assignments) for *logged-in* users.
    Anonymous visitors are bounced to ``/`` — the landing page already shows
    the same generic anni status. Countdown derives from the stamp poller, so
    it matches ``/v1/outbound/stamp``."""
    if (await auth.current_user(request)) is None:
        return RedirectResponse("/", status_code=303)

    event = await get_active_event()
    organizer = _organizer(event)
    parties: list[dict] = []
    if event is not None:
        rows = await Party.filter(event=event).select_related("host").order_by("ordinal")
        parties = [
            {
                "ordinal": p.ordinal,
                "host": p.host.mc_username if p.host else None,
                "host_avatar": _avatar(p.host.mc_uuid, 24) if p.host else None,
                "world": p.world,
                "stage": p.stage,
            }
            for p in rows
        ]
    return render(
        request,
        "public/overview.html",
        event=event,
        organizer=organizer,
        parties=parties,
    )


@router.get("/toggle-cb", include_in_schema=False)
async def toggle_colourblind(request: Request):
    """Flip the per-user colourblind variant and bounce back where we came
    from. Present on every interface (spec hard requirement)."""
    target = request.query_params.get("next") or request.headers.get("referer") or "/"
    resp = RedirectResponse(target, status_code=303)
    set_colourblind(resp, not colourblind(request))
    return resp


@router.get("/toggle-label", include_in_schema=False)
async def toggle_label(request: Request):
    """Flip a board label-density preference (``which`` = roles|status) and
    bounce back. Only meaningful with CB off — under CB the hard rule keeps
    labels on regardless; we still flip the underlying cookie so the choice
    takes effect if the user later turns CB off."""
    which = request.query_params.get("which")
    target = request.query_params.get("next") or "/staff/board"
    resp = RedirectResponse(target, status_code=303)
    if which in ("roles", "status"):
        set_label_pref(resp, which, not label_pref_on(request, which))
    elif which == "pin":
        set_pin(resp, not pin_legend(request))
    # Unknown facet -> a safe bounce, no cookie change, never a crash.
    return resp
