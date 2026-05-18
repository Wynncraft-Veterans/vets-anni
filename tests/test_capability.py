"""Core/Fill classification + weapon write-validation (incl. the degrade path).

The "empty catalog => UNVERIFIED (accept+flag)" rule is a deliberate UX
choice; pin it so a future "fail closed" refactor is a conscious decision.
"""

from __future__ import annotations

from app.constants import MAX_WEAPONS_PER_CAPABILITY
from app.domain import capability as cap

CATALOG = {"idol": "wand", "labyrinth": "relik", "stratiformis": "bow"}


def test_core_vs_fill():
    assert cap.is_core(0) is False
    assert cap.classify(0) == "fill"
    assert cap.is_core(1) is True
    assert cap.classify(3) == "core"


def test_validate_weapon_against_a_populated_catalog():
    r = cap.validate_weapon("Idol", CATALOG)
    assert r.check is cap.WeaponCheck.VALID and r.subtype == "wand"
    assert cap.validate_weapon("Totally Fake", CATALOG).check is cap.WeaponCheck.INVALID
    assert cap.validate_weapon("", CATALOG).check is cap.WeaponCheck.INVALID


def test_empty_catalog_degrades_to_unverified_not_blocked():
    r = cap.validate_weapon("Idol", {})
    assert r.check is cap.WeaponCheck.UNVERIFIED
    assert r.subtype is None


def test_per_role_weapon_cap():
    assert cap.weapons_within_cap(0) is True
    assert cap.weapons_within_cap(MAX_WEAPONS_PER_CAPABILITY) is True
    assert cap.weapons_within_cap(MAX_WEAPONS_PER_CAPABILITY + 1) is False
    assert cap.weapons_within_cap(-1) is False
