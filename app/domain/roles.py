"""Role helpers — thin, pure accessors over ``app.constants``.

Keeps routers/templates from reaching into the constants tables directly (one
place to change if the role model evolves).
"""

from __future__ import annotations

from app.constants import (
    ASSIGNABLE_ROLES,
    CAPABILITY_ROLES,
    ROLE_GUIDANCE,
    ROLE_STYLES,
    Role,
    RoleGuidance,
    RoleStyle,
)


def capability_roles() -> tuple[Role, ...]:
    """The five core roles a user can register a capability for (no FILL)."""
    return CAPABILITY_ROLES


def assignable_roles() -> tuple[Role, ...]:
    """Core roles + FILL — what an organiser may assign on the board."""
    return ASSIGNABLE_ROLES


def guidance(role: Role) -> RoleGuidance:
    """Docs-sourced requirement/gameplay/builds text for the add-capability UI."""
    return ROLE_GUIDANCE[role]


def style(role: Role) -> RoleStyle:
    """The shared palette entry + glyph/label for ``role`` (never colour-only)."""
    return ROLE_STYLES[role]


def parse(value: str | None) -> Role | None:
    """Best-effort ``str -> Role`` (form input); ``None`` if not a core/fill role."""
    if not value:
        return None
    try:
        return Role(value.strip().lower())
    except ValueError:
        return None
