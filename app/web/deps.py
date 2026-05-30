"""Shared web plumbing: Jinja env, signed-cookie sessions, colourblind toggle.

Sessions are stateless signed cookies (itsdangerous) — no session table. The
auth model is intentionally low-trust (a coordination tool, not a security
boundary); see ``app/web/auth.py`` and ``.claude/integration.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.constants import (
    PARTY_STAGE_LABELS,
    ROLE_STYLES,
    STATUS_STYLES,
    STYLES,
    UNASSIGNED_STYLE,
)
from app.domain.colourblind import role_chip, status_chip
from app.settings import get_settings

_settings = get_settings()
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


def asset(path: str) -> str:
    """``/static/<path>`` with a ``?v=<mtime>`` cache-buster.

    CSS/JS edits don't restart the server (uvicorn only watches ``*.py``), so
    without this the browser keeps serving a stale stylesheet — the cause of
    "the change didn't take" loops. The mtime is read per call (cheap) so the
    URL changes the instant the file is saved, no restart needed.
    """
    rel = path.lstrip("/")
    try:
        v = int((_STATIC_DIR / rel).stat().st_mtime)
    except OSError:
        v = 0
    return f"/static/{rel}?v={v}"

#: Jinja environment. Templates use the glassmorphism reference CSS; colour is
#: never the only signal (macros emit glyph + label + pattern too).
env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
# Expose the palettes to every template so macros can render role/status chips.
env.globals.update(
    STYLES=STYLES,
    ROLE_STYLES=ROLE_STYLES,
    STATUS_STYLES=STATUS_STYLES,
    UNASSIGNED_STYLE=UNASSIGNED_STYLE,
    # Chip builders so the board legend renders the *same* colour-var + glyph
    # + pattern channels the macros do (one source — no inline var maps).
    role_chip=role_chip,
    status_chip=status_chip,
    PARTY_STAGE_LABELS=PARTY_STAGE_LABELS,
    PUBLIC_BASE_URL=_settings.public_base_url,
    asset=asset,
)

_SESSION_COOKIE = "anni_session"
_CB_COOKIE = "cb"
_serializer = URLSafeSerializer(_settings.session_secret, salt="anni-session")


# --- sessions --------------------------------------------------------------
def read_session(request: Request) -> dict[str, Any]:
    """Return the decoded session dict (``{}`` if absent/tampered)."""
    raw = request.cookies.get(_SESSION_COOKIE)
    if not raw:
        return {}
    try:
        return _serializer.loads(raw)
    except BadSignature:
        return {}


def write_session(response: Response, data: dict[str, Any]) -> None:
    response.set_cookie(
        _SESSION_COOKIE,
        _serializer.dumps(data),
        httponly=True,
        samesite="lax",
        secure=not _settings.debug,
        max_age=60 * 60 * 24 * 7,
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(_SESSION_COOKIE)


# --- colourblind toggle ----------------------------------------------------
def colourblind(request: Request) -> bool:
    """True when the per-user colourblind variant is active (``cb`` cookie)."""
    return request.cookies.get(_CB_COOKIE) == "1"


def set_colourblind(response: Response, on: bool) -> None:
    if on:
        response.set_cookie(_CB_COOKIE, "1", max_age=60 * 60 * 24 * 365,
                            samesite="lax")
    else:
        response.delete_cookie(_CB_COOKIE)


# --- board label-density toggles -------------------------------------------
# Per-user "show the text tag?" prefs for the board person cards. Default OFF
# in both modes — the role-card background, status border colour+pattern, and
# capability dots already carry the signal, so the text tag is opt-in density.
# The toggle is interactive in CB too: aria-label on the person root still
# announces role+status for screen-reader users regardless.
_LABEL_COOKIES = {"roles": "lbl_roles", "status": "lbl_status"}


def label_visible(request: Request, which: str) -> bool:
    """Whether the role/status text tag actually renders on a person card."""
    return request.cookies.get(_LABEL_COOKIES[which]) == "1"


def set_label_pref(response: Response, which: str, on: bool) -> None:
    name = _LABEL_COOKIES[which]
    if on:
        response.set_cookie(name, "1", max_age=60 * 60 * 24 * 365,
                            samesite="lax")
    else:
        response.delete_cookie(name)


# Pin the legend/configs bar to the top while scrolling. Unlike the label
# prefs this defaults **on**, so "no cookie" == pinned and we only ever store
# the explicit opt-OUT ("0"); clearing it returns to the default.
_PIN_COOKIE = "cfg_pin"


def pin_legend(request: Request) -> bool:
    """True (default) unless the user explicitly turned pinning off."""
    return request.cookies.get(_PIN_COOKIE) != "0"


def set_pin(response: Response, on: bool) -> None:
    if on:
        response.delete_cookie(_PIN_COOKIE)          # back to default (on)
    else:
        response.set_cookie(_PIN_COOKIE, "0", max_age=60 * 60 * 24 * 365,
                            samesite="lax")


# Capability-dot popover trigger — hover (default, cookie absent/"") or click
# ("click"). Same cookie family as cb/pin/label prefs; we only store the
# explicit non-default ("click") so clearing the cookie reverts to hover. The
# value is exposed as a body class so the choice survives the WS-driven
# #board refreshes with no JS needed for the hover path.
_DOT_MODE_COOKIE = "cfg_dot_mode"


def dot_click_mode(request: Request) -> bool:
    """True when the user opted into click-based capability-dot info popups."""
    return request.cookies.get(_DOT_MODE_COOKIE) == "click"


def set_dot_click_mode(response: Response, on: bool) -> None:
    if on:
        response.set_cookie(_DOT_MODE_COOKIE, "click",
                            max_age=60 * 60 * 24 * 365, samesite="lax")
    else:
        response.delete_cookie(_DOT_MODE_COOKIE)   # back to default (hover)


# Per-user opt-in: a destination dropdown on each person card as an
# alternative to drag-drop. Default OFF (cookie absent → no dropdown,
# drag-drop only) so the board stays uncluttered for staff who don't want it.
_DROPDOWN_ASSIGN_COOKIE = "cfg_dropdown_assign"


def dropdown_assign(request: Request) -> bool:
    """True when the user opted into the per-card destination dropdown."""
    return request.cookies.get(_DROPDOWN_ASSIGN_COOKIE) == "1"


def set_dropdown_assign(response: Response, on: bool) -> None:
    if on:
        response.set_cookie(_DROPDOWN_ASSIGN_COOKIE, "1",
                            max_age=60 * 60 * 24 * 365, samesite="lax")
    else:
        response.delete_cookie(_DROPDOWN_ASSIGN_COOKIE)


# Per-user collapsed parties — a CSV of party ids in a cookie (same family as
# cb/pin). It MUST be server-side: the board re-renders on every WS tick, so
# a client-only collapse would pop back open; and it's per-user, so it never
# goes through board_hub / WS broadcast. Stale ids from a wiped event simply
# never match a current party (harmless), so no pruning is needed.
_COLLAPSE_COOKIE = "collapsed_parties"


def collapsed_parties(request: Request) -> set[str]:
    """The set of party ids this user has collapsed (``{}`` if none)."""
    raw = request.cookies.get(_COLLAPSE_COOKIE) or ""
    return {p for p in raw.split(",") if p}


def set_collapsed_parties(response: Response, ids: set[str]) -> None:
    if ids:
        response.set_cookie(_COLLAPSE_COOKIE, ",".join(sorted(ids)),
                            max_age=60 * 60 * 24 * 365, samesite="lax")
    else:
        response.delete_cookie(_COLLAPSE_COOKIE)


# --- rendering -------------------------------------------------------------
def render(request: Request, template: str, **context: Any) -> Response:
    """Render a Jinja template with the common context every page needs."""
    from fastapi.responses import HTMLResponse

    session = read_session(request)
    ctx: dict[str, Any] = {
        "request": request,
        "cb": colourblind(request),
        "session": session,
        "user_uuid": session.get("mc_uuid") if session.get("kind") == "user" else None,
        "is_staff": session.get("kind") == "staff",
        "debug": _settings.debug,
        # Effective board label visibility (CB forces both on); base.html turns
        # these into body classes, the board controls box reflects them.
        "label_roles": label_visible(request, "roles"),
        "label_status": label_visible(request, "status"),
        "pin_legend": pin_legend(request),
        "dot_click_mode": dot_click_mode(request),
        "dropdown_assign": dropdown_assign(request),
        # Cookie-derived by default; the collapse toggle route passes a fresh
        # set via **context so its own response reflects the new state (the
        # cookie it sets isn't visible until the next request).
        "collapsed_parties": collapsed_parties(request),
        **context,
    }
    return HTMLResponse(env.get_template(template).render(**ctx))


def json_default(obj: Any) -> Any:  # pragma: no cover - serialization helper
    """``json.dumps`` fallback for enums/dataclasses used in WS frames."""
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def to_json(data: Any) -> str:
    return json.dumps(data, default=json_default, separators=(",", ":"))
