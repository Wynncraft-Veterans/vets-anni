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

from app.constants import ROLE_STYLES, STATUS_STYLES, STYLES, UNASSIGNED_STYLE
from app.settings import get_settings

_settings = get_settings()
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

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
    PUBLIC_BASE_URL=_settings.public_base_url,
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
