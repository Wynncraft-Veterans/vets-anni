"""Capability rules — Core vs Fill, and weapon-write validation.

Pure (operates on counts + the cached catalog dict; the router does the DB
work). Spec: a user with ≥1 declared :class:`RoleCapability` is **Core**;
zero => **Fill** (gets a red warning bar — fill slots aren't guaranteed).

Weapon constraints (``.claude/domain_rules.md``), enforced at write time:
1. every weapon must be real — validated against the cached WAPI catalog;
2. ≤ ``MAX_WEAPONS_PER_CAPABILITY`` weapons *per (player, role)*.

Catalog resilience: if the weapons poll has never succeeded the catalog is
empty; rather than block every edit we return ``UNVERIFIED`` (the router
accepts but flags it) — far better UX than "weapons unavailable, try later"
for a low-trust coordination tool.

Separately, :func:`is_unusual_build` drives the modal's advisory "are you
sure?" warning — it never blocks a save, and never names the reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum, auto

from app.constants import (
    MAX_WEAPONS_PER_CAPABILITY,
    ROLE_CLASS_LEVELS,
    ROLE_WEAPONS,
    Role,
)


def classify(capability_count: int) -> str:
    """``"core"`` if the player declared ≥1 capability, else ``"fill"``."""
    return "core" if capability_count >= 1 else "fill"


def is_core(capability_count: int) -> bool:
    return capability_count >= 1


# First line is the headline (the template bolds it); the `\n` splits it from
# the body (rendered with white-space:pre-line — see _capacity.html). Plain
# text only — emphasis is a presentation concern, applied in the template.
FILL_WARNING = (
    "You have not indicated any role capabilities and, as such, are set to "
    "attend as a fill!\n"
    "Fill slots are unfortunately in limited supply and high demand! "
    "If you are able to fulfil a role, please add it!"
)


class WeaponCheck(StrEnum):
    VALID = auto()       # found in the catalog
    INVALID = auto()     # catalog is populated and the name is not in it
    UNVERIFIED = auto()  # catalog empty (poll not yet succeeded) — accept+flag


@dataclass(frozen=True)
class WeaponResult:
    check: WeaponCheck
    subtype: str | None  # bow/spear/wand/dagger/relik when known


def validate_weapon(name: str, catalog: dict[str, str]) -> WeaponResult:
    """Validate one weapon name against ``state.weapons_by_name``.

    ``catalog`` maps ``name_lower -> subtype``. Empty catalog =>
    :class:`WeaponCheck.UNVERIFIED` (see module docstring).
    """
    cleaned = name.strip()
    if not cleaned:
        return WeaponResult(WeaponCheck.INVALID, None)
    if not catalog:
        return WeaponResult(WeaponCheck.UNVERIFIED, None)
    subtype = catalog.get(cleaned.lower())
    if subtype is None:
        return WeaponResult(WeaponCheck.INVALID, None)
    return WeaponResult(WeaponCheck.VALID, subtype)


def weapons_within_cap(new_total: int) -> bool:
    """True iff a capability would still hold ≤ the per-role weapon cap."""
    return 0 <= new_total <= MAX_WEAPONS_PER_CAPABILITY


CAP_EXCEEDED = (
    f"A capability can list at most {MAX_WEAPONS_PER_CAPABILITY} weapons. "
    "Remove one before adding another (the cap is per role — a separate "
    f"{MAX_WEAPONS_PER_CAPABILITY} for each role is fine)."
)


# Same shape as FILL_WARNING: first line is the headline, a blank line
# separates paragraphs. Deliberately silent on *which* rule tripped.
UNUSUAL_BUILD_WARNING = (
    "Just to double check, are you sure?\n"
    "Something about your build is unusual for this role!\n\n"
    "Uncommon builds aren't inherently problematic, but if you plan to use "
    "one, please make sure that you are confident it will fulfil this role's "
    "requirements!\n\n"
    "If you have any questions, please don't hesitate to ask!"
)

#: WAPI names a mythic's masterwork "Masterwork <base>" (its display name is
#: the bare base), so stripping this recovers the weapon it is a version of.
_MASTERWORK_PREFIX = "masterwork "


def _is_unusual_weapon(
    role: Role, name: str, mythics: frozenset[str], max_level: int | None
) -> bool:
    allowed = ROLE_WEAPONS.get(role)
    if allowed is not None:
        return name.removeprefix(_MASTERWORK_PREFIX) not in allowed
    floor = ROLE_CLASS_LEVELS.get(role)
    if floor is None:
        return False
    non_mythic = bool(mythics) and name not in mythics
    underlevelled = max_level is not None and max_level <= floor
    if role is Role.PRIMARY:
        return non_mythic or underlevelled
    return non_mythic and underlevelled


def is_unusual_build(
    role: Role,
    weapons: Iterable[str],
    *,
    catalog: dict[str, str],
    mythics: frozenset[str],
    max_level: int | None,
) -> bool:
    """True iff any listed weapon is unusual for ``role`` (``ROLE_WEAPONS`` /
    ``ROLE_CLASS_LEVELS`` in constants).

    Judges only what it can confirm, since a false alarm is worse than a
    missed nudge: a name the catalog doesn't know is left to write-time
    validation, an empty ``mythics`` means rarity is unknown, and
    ``max_level=None`` (characters hidden / WAPI down) means the level half of
    a rule can't trip. A capability with no weapons is never unusual.
    """
    return any(
        _is_unusual_weapon(role, key, mythics, max_level)
        for key in (w.strip().lower() for w in weapons)
        if key in catalog
    )
