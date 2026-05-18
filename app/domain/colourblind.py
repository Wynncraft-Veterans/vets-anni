"""Chip-context builders — colour is *never* the only signal.

Templates render role backgrounds and status borders through these so every
chip carries its glyph + accessible label (+ a border pattern for statuses)
regardless of the ``cb`` cookie. The cookie only swaps the seven ``--c-*``
hues in CSS (see ``static/css/colourblind.css``); the non-colour channels
emitted here are identical in both modes, which is exactly what the spec's
"usable colourblind variant" hard-requirement needs.

Pure data shaping — no request/cookie logic (that's ``app/web/deps.py``).
"""

from __future__ import annotations

from typing import TypedDict

from app.constants import (
    ROLE_STYLES,
    STATUS_STYLES,
    UNASSIGNED_STYLE,
    PresenceStatus,
    Role,
)

#: Role -> the CSS custom-property the stylesheet exposes (``body.cb`` swaps
#: the underlying ``--c-*`` hue these alias, so we never inline a hex here).
_ROLE_VAR: dict[Role, str] = {
    Role.PRIMARY: "--role-primary",
    Role.SECONDARY: "--role-secondary",
    Role.TERTIARY: "--role-tertiary",
    Role.HEALER: "--role-healer",
    Role.TANK: "--role-tank",
    Role.FILL: "--role-fill",
}
_STATUS_VAR: dict[PresenceStatus, str] = {
    PresenceStatus.OFFLINE_GONE: "--st-gone",
    PresenceStatus.OFFLINE_HARD: "--st-offhard",
    PresenceStatus.OFFLINE_SOFT: "--st-offsoft",
    PresenceStatus.ONLINE_ELSEWHERE: "--st-elsewhere",
    PresenceStatus.ONLINE_WORLD: "--st-world",
    PresenceStatus.ONLINE_PARTY: "--st-party",
    PresenceStatus.UNKNOWN: "--st-unknown",
}


class RoleChip(TypedDict):
    css_var: str       # raw identifying hue (e.g. "--role-tank") — glyph swatch
    css_var_dark: str  # legible dark shade (e.g. "--role-tank-dark") — chip body
    glyph: str         # e.g. "TANK"
    label: str         # aria-label / title


class StatusChip(TypedDict):
    css_var: str
    glyph: str
    label: str
    pattern: str   # data-pattern -> non-colour online/offline encoding


def role_chip(role: Role | None) -> RoleChip:
    """Background chip for an assigned role (``None`` => grey 'unassigned').

    The chip *body* uses the legible ``-dark`` shade (white text is readable
    on it for every role — the bright base hues like green/yellow are not);
    the small glyph swatch keeps the *raw* identifying hue (and that one still
    CB-swaps under ``body.cb``). Colour is never load-bearing anyway — the
    glyph + label carry the meaning.
    """
    if role is None:
        var = "--role-unassigned"
        return RoleChip(css_var=var, css_var_dark=f"{var}-dark",
                        glyph=UNASSIGNED_STYLE.glyph, label=UNASSIGNED_STYLE.label)
    s = ROLE_STYLES[role]
    var = _ROLE_VAR[role]
    return RoleChip(css_var=var, css_var_dark=f"{var}-dark",
                    glyph=s.glyph, label=s.label)


def status_chip(status: PresenceStatus) -> StatusChip:
    """Border chip for a presence status — colour + glyph + label + pattern."""
    s = STATUS_STYLES[status]
    return StatusChip(
        css_var=_STATUS_VAR[status],
        glyph=s.glyph,
        label=s.label,
        pattern=s.pattern,
    )
