"""weapons_poller — the validated weapons catalog (hourly, OWN token).

Feeds the add-capability autocomplete + write-time validation: a capability
weapon must be a real Wynncraft weapon (``app/domain/capability.py``).

We use the v3 **advanced** item search — ``POST /v3/item/search?fullResult``
with ``{"type": ["weapon"]}`` — which is the only correct way to *enumerate*
the catalog. (``GET /v3/item/search/{q}`` is a fuzzy *name* search and the
field is ``subType``, not ``weaponType`` — getting either wrong is why an
earlier attempt produced an empty/garbage catalog and weapons displayed with
the wrong subtype.) The response is a JSON **array** of item objects; we map
both the display and internal names (lower-cased) to ``subType``.

Resilience over completeness: a failed/odd tick keeps the last-good catalog;
a never-built catalog degrades validation to "accepted, unverified" rather
than blocking every capability edit (see ``app/domain/capability.py``). The
~1 h cadence keeps token spend negligible.
"""

from __future__ import annotations

import logging

from app.constants import WEAPON_SUBTYPES
from app.services.loop import poll_forever
from app.services.state import AppState
from app.services.wapi import PRIO_LOW, WapiError, get_wapi
from app.settings import Settings

logger = logging.getLogger("anni.weapons")


def _harvest(payload: object) -> dict[str, str]:
    """``payload`` is the v3 search array -> ``{name_lower: subType}``.

    Maps both ``displayName`` and ``internalName`` so a user can type either
    (they usually type the display name, e.g. "Idol"). Non-weapons / unknown
    subtypes are skipped defensively.
    """
    catalog: dict[str, str] = {}
    if not isinstance(payload, list):
        return catalog
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "weapon":
            continue
        subtype = str(item.get("subType", "")).lower()
        if subtype not in WEAPON_SUBTYPES:
            continue
        for key in ("displayName", "internalName"):
            name = item.get(key)
            if isinstance(name, str) and name.strip():
                catalog[name.strip().lower()] = subtype
    return catalog


async def _tick(state: AppState, settings: Settings) -> None:
    try:
        payload = await get_wapi().post_json(
            "item/search?fullResult", {"type": ["weapon"]}, priority=PRIO_LOW
        )
    except WapiError as exc:
        logger.info("weapons: catalog fetch skipped (%s) — keeping last-good", exc)
        return

    catalog = _harvest(payload)
    if catalog:
        state.weapons_by_name = catalog
        state.touch("weapons_fetched_at")
        logger.info(
            "weapons catalog rebuilt: %d names across %d subtypes",
            len(catalog), len(set(catalog.values())),
        )
    else:
        logger.warning("weapons catalog empty this tick — keeping last-good")


async def run(state: AppState, settings: Settings) -> None:
    await poll_forever(
        "weapons",
        lambda: settings.weapons_poll_seconds,
        lambda: _tick(state, settings),
    )
