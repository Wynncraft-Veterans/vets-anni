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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from app.constants import MAX_WEAPONS_PER_CAPABILITY


def classify(capability_count: int) -> str:
    """``"core"`` if the player declared ≥1 capability, else ``"fill"``."""
    return "core" if capability_count >= 1 else "fill"


def is_core(capability_count: int) -> bool:
    return capability_count >= 1


FILL_WARNING = (
    "You have not indicated any role capabilities and, as such, are set to "
    "attend as a fill. Fill slots are unfortunately in limited supply, and "
    "not guaranteed. If you can fill a role, please add one below!"
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
