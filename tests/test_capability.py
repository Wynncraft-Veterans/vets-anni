"""Core/Fill classification + weapon write-validation (incl. the degrade path).

The "empty catalog => UNVERIFIED (accept+flag)" rule is a deliberate UX
choice; pin it so a future "fail closed" refactor is a conscious decision.
"""

from __future__ import annotations

import pytest

from app.constants import MAX_WEAPONS_PER_CAPABILITY, Role
from app.domain import capability as cap
from app.domain import identity
from app.services.state import AppState
from app.services.weapons_poller import _harvest

# Real Wynncraft v3 `subType` values (verified via POST /v3/item/search).
CATALOG = {"idol": "spear", "labyrinth": "bow", "stratiformis": "bow"}


def test_core_vs_fill():
    assert cap.is_core(0) is False
    assert cap.classify(0) == "fill"
    assert cap.is_core(1) is True
    assert cap.classify(3) == "core"


def test_validate_weapon_against_a_populated_catalog():
    r = cap.validate_weapon("Idol", CATALOG)
    assert r.check is cap.WeaponCheck.VALID and r.subtype == "spear"
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


# --- unusual-build warning --------------------------------------------------
# Real v3 names/tiers (POST /v3/item/search, 2026-09-28): a masterwork's
# internalName is "Masterwork <base>" and its displayName the bare base, so
# the catalog holds both; Pareidolia is a legendary.
BUILD_CATALOG = {
    "idol": "spear", "masterwork idol": "spear",
    "lament": "wand", "masterwork lament": "wand",
    "guardian": "spear", "masterwork guardian": "spear",
    "absolution": "relik", "pareidolia": "relik",
}
MYTHICS = frozenset(BUILD_CATALOG) - {"pareidolia"}


def _unusual(role, weapons, max_level=121, mythics=MYTHICS):
    return cap.is_unusual_build(
        role, weapons, catalog=BUILD_CATALOG, mythics=mythics, max_level=max_level,
    )


def test_primary_wants_a_mythic_and_a_class_above_120():
    assert _unusual(Role.PRIMARY, ["Idol"], 121) is False
    assert _unusual(Role.PRIMARY, ["Pareidolia"], 121) is True
    assert _unusual(Role.PRIMARY, ["Idol"], 120) is True  # "above", not "at"


@pytest.mark.parametrize("role, floor", [(Role.SECONDARY, 110), (Role.TERTIARY, 100)])
def test_lower_dps_roles_are_unusual_only_with_neither(role, floor):
    assert _unusual(role, ["Pareidolia"], floor) is True
    assert _unusual(role, ["Pareidolia"], floor + 1) is False
    assert _unusual(role, ["Idol"], 1) is False


@pytest.mark.parametrize("role, usual", [
    (Role.HEALER, ["Lament", "Masterwork Lament", "Absolution"]),
    (Role.TANK, ["Guardian", "masterwork guardian"]),
])
def test_support_roles_expect_their_weapons_or_a_masterwork(role, usual):
    assert _unusual(role, usual) is False
    assert _unusual(role, ["Idol"]) is True
    assert _unusual(role, usual[:1] + [" IDOL "]) is True  # any one weapon trips it


def test_unusual_build_stays_silent_on_what_it_cannot_confirm():
    assert _unusual(Role.PRIMARY, []) is False
    assert _unusual(Role.HEALER, ["Totally Fake"]) is False  # validation's job
    # Levels unknown (hidden characters / WAPI down): rarity alone decides.
    assert _unusual(Role.PRIMARY, ["Idol"], None) is False
    assert _unusual(Role.PRIMARY, ["Pareidolia"], None) is True
    assert _unusual(Role.SECONDARY, ["Pareidolia"], None) is False
    # Rarity unknown (no mythic set): levels alone decide.
    assert _unusual(Role.PRIMARY, ["Pareidolia"], 121, frozenset()) is False
    assert _unusual(Role.PRIMARY, ["Pareidolia"], 50, frozenset()) is True
    assert _unusual(Role.SECONDARY, ["Pareidolia"], 50, frozenset()) is False


def test_harvest_collects_mythics_under_both_names():
    catalog, mythics = _harvest([
        {"type": "weapon", "subType": "wand", "tier": "mythic",
         "internalName": "Masterwork Lament", "displayName": "Lament"},
        {"type": "weapon", "subType": "relik", "tier": "legendary",
         "internalName": "Pareidolia", "displayName": "Pareidolia"},
    ])
    assert catalog == {"masterwork lament": "wand", "lament": "wand",
                       "pareidolia": "relik"}
    assert mythics == {"masterwork lament", "lament"}


async def test_max_class_level_caches_hits_and_misses(monkeypatch):
    calls = []
    profiles = iter([
        {"characters": {"a": {"level": 106}, "b": {"level": 121}, "c": {}}},
        {"characters": {}},  # hidden character data
    ])

    async def fake(uuid, *, full=False):
        calls.append((uuid, full))
        return next(profiles)

    monkeypatch.setattr(identity, "_fetch_wapi_profile", fake)
    state = AppState()
    assert await identity.max_class_level("u1", state) == 121
    assert await identity.max_class_level("u1", state) == 121
    assert await identity.max_class_level("u2", state) is None
    assert await identity.max_class_level("u2", state) is None
    assert calls == [("u1", True), ("u2", True)]  # one fullResult call each
