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
# Per-user "show the text tag?" prefs for the board person cards. When CB is
# OFF the role is fully derivable from the card background and the status from
# the border colour+pattern, so the text tags are noise — hidden by default,
# opt back in here. When CB is ON the spec's HARD rule (colour is never the
# only signal) overrides everything: glyph+label are ALWAYS shown, the toggle
# is inert. The cookie only records the *non-cb* preference; CB wins at render.
_LABEL_COOKIES = {"roles": "lbl_roles", "status": "lbl_status"}


def label_pref_on(request: Request, which: str) -> bool:
    """The raw per-user cookie preference (independent of CB)."""
    return request.cookies.get(_LABEL_COOKIES[which]) == "1"


def label_visible(request: Request, which: str) -> bool:
    """Whether the role/status text tag actually renders on a person card:
    forced on under colourblind mode (hard spec rule), else the cookie pref
    (default off — colour alone conveys it when CB is off)."""
    return colourblind(request) or label_pref_on(request, which)


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
