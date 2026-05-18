"""weapons_poller — the validated weapons catalog (hourly, OWN token).

Feeds the add-capability autocomplete + write-time validation: a capability
weapon must be a real Wynncraft weapon (``app/domain/capability.py``). We hit
WAPI ``/v3/item/search/{query}`` once per weapon subtype (low priority — it
must never delay an interactive player lookup) and fold the results into
``state.weapons_by_name`` (``name_lower -> subtype``).

Resilience over completeness: if the catalog cannot be (re)built this tick the
*last-good* one stays; if it has never built, validation degrades to
"accepted but unverified" rather than blocking every capability edit (see
``app/domain/capability.py``). The ~1 h cadence keeps token spend negligible.
"""

from __future__ import annotations

import logging

from app.constants import WEAPON_SUBTYPES
from app.services.loop import poll_forever
from app.services.state import AppState
from app.services.wapi import PRIO_LOW, WapiError, get_wapi
from app.settings import Settings

logger = logging.getLogger("anni.weapons")


def _harvest(payload: dict, want: str, into: dict[str, str]) -> None:
    """Record ``name_lower -> subtype`` for weapon entries of subtype ``want``.

    v3 item search returns ``{<ItemName>: {type, weaponType, ...}, ...}``.
    Only weapons whose ``weaponType`` is in our subtype set are kept; anything
    else (armour, ingredients, the odd shape change) is ignored.
    """
    if not isinstance(payload, dict):
        return
    for name, info in payload.items():
        if not isinstance(info, dict):
            continue
        if str(info.get("type", "")).lower() != "weapon":
            continue
        subtype = str(info.get("weaponType", "")).lower()
        if subtype in WEAPON_SUBTYPES and subtype == want:
            into[str(name).strip().lower()] = subtype


async def _tick(state: AppState, settings: Settings) -> None:
    catalog: dict[str, str] = {}
    for subtype in WEAPON_SUBTYPES:
        try:
            payload = await get_wapi().get_json(
                f"item/search/{subtype}", priority=PRIO_LOW
            )
        except WapiError as exc:
            logger.info("weapons: %s search skipped (%s)", subtype, exc)
            continue
        before = len(catalog)
        _harvest(payload, subtype, catalog)
        logger.debug("weapons: %s -> +%d", subtype, len(catalog) - before)

    if catalog:
        state.weapons_by_name = catalog
        state.touch("weapons_fetched_at")
        logger.info("weapons catalog rebuilt: %d entries", len(catalog))
    else:
        logger.warning("weapons catalog empty this tick — keeping last-good")


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "weapons",
        lambda: settings.weapons_poll_seconds,
        lambda: _tick(state, settings),
    )
