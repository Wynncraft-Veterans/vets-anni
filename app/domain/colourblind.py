"""Chip-context builders — colour is *never* the only signal.

Templates render role backgrounds and status borders through these so every
chip carries its glyph + accessible label (+ traffic-light lamps for
statuses) regardless of the ``cb`` cookie. The cookie only swaps the seven ``--c-*``
hues in CSS (see ``static/css/colourblind.css``); the non-colour channels
emitted here are identical in both modes, which is exactly what the spec's
"usable colourblind variant" hard-requirement needs.

Pure data shaping — no request/cookie logic (that's ``app/web/deps.py``).
"""

from __future__ import annotations

from typing import TypedDict

from app.constants import (
    ROLE_STYLES,
    RSVP_STYLES,
    STATUS_STYLES,
    UNASSIGNED_STYLE,
    PresenceStatus,
    Role,
    RsvpState,
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
    PresenceStatus.ONLINE_PARTY: "--st-party",
    PresenceStatus.ONLINE_WORLD: "--st-world",
    PresenceStatus.ONLINE_ELSEWHERE: "--st-elsewhere",
    PresenceStatus.OFFLINE: "--st-offline",
    PresenceStatus.UNKNOWN: "--st-unknown",
}
_RSVP_VAR: dict[RsvpState, str] = {
    RsvpState.NONE: "--rsvp-none",
    RsvpState.HARD: "--rsvp-hard",
    RsvpState.SOFT: "--rsvp-soft",
    RsvpState.REVOKED: "--rsvp-revoked",
}


class RoleChip(TypedDict):
    css_var: str       # raw identifying hue (e.g. "--role-tank") — glyph swatch
    css_var_dark: str  # legible dark shade (e.g. "--role-tank-dark") — chip body
    glyph: str         # e.g. "TANK"
    code: str          # e.g. "TA" — the cb legend key for the capability pips
    label: str         # aria-label / title


class StatusChip(TypedDict):
    css_var: str       # the border hue
    css_var_dark: str  # its dark shade — the hairline just inside the border
    glyph: str
    label: str         # short name ("In Party")
    description: str   # the full sentence, for a hover title
    lamps: str     # "100" … top→bottom, 1 = lit — the cb traffic light


class RsvpChip(TypedDict):
    css_var: str       # bright hue — the ticket's strokes
    css_var_dark: str  # matching dark shade — the plate behind it
    icon: str      # shape name in macros/icons.html — the greyscale channel
    label: str
    state: str     # the raw RsvpState value (data-attr / CSS hook)


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
                        glyph=UNASSIGNED_STYLE.glyph, code=UNASSIGNED_STYLE.code,
                        label=UNASSIGNED_STYLE.label)
    s = ROLE_STYLES[role]
    var = _ROLE_VAR[role]
    return RoleChip(css_var=var, css_var_dark=f"{var}-dark",
                    glyph=s.glyph, code=s.code, label=s.label)


def status_chip(status: PresenceStatus) -> StatusChip:
    """Border chip for a presence status — colour + glyph + label + lamps.

    Two channels of the one hue, same split as :func:`role_chip`: the bright
    shade is the card's outline and the dark one is a hairline just inside
    it. The hairline carries no information the border doesn't — it is there
    because a bright status band sits directly on a bright role background,
    and without an edge between them the two colours bleed into each other.
    """
    s = STATUS_STYLES[status]
    var = _STATUS_VAR[status]
    return StatusChip(
        css_var=var,
        css_var_dark=f"{var}-dark",
        glyph=s.glyph,
        label=s.label,
        description=s.description,
        lamps=s.lamps,
    )


def rsvp_chip(state: RsvpState) -> RsvpChip:
    """Badge for the RSVP axis — colour + glyph + label.

    Its four ticket *shapes* are the non-colour channel — what is
    printed on each ticket, whether the outline is solid or dotted, and
    whether the silhouette is whole or torn.

    Two colour channels, same hue: ``css_var`` is the bright stroke and
    ``css_var_dark`` the plate behind it (same split as
    :func:`role_chip`). The plate is what lets one icon file read identically
    on a dark role-coloured card and on the pale legend — it brings its own
    background instead of relying on a keyline to survive both.
    """
    s = RSVP_STYLES[state]
    var = _RSVP_VAR[state]
    return RsvpChip(
        css_var=var,
        css_var_dark=f"{var}-dark",
        icon=s.icon,
        label=s.label,
        state=state.value,
    )
