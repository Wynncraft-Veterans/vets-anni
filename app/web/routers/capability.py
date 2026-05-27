"""App1 — role-capability CRUD + the weapons autocomplete.

The add/edit modal quotes the docs-sourced role guidance + links the
wynnvets.org gameplay/builds anchors (spec). Weapons are validated against
the cached WAPI catalog at write time (``app.domain.capability``); an empty
catalog degrades to "accepted, unverified" rather than blocking the edit.

Every mutation returns the refreshed Role-Capacity fragment so HTMX swaps it
in place (no full reload). ``success_count`` is never user-editable — it is
incremented only by the Phase-2 grace-wipe for WIN parties.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.constants import MAX_WEAPONS_PER_CAPABILITY, ConfidenceLevel
from app.db.models import RoleCapability, RoleCapabilityWeapon
from app.domain import capability as cap_domain
from app.domain.roles import capability_roles, guidance, parse as parse_role
from app.web import auth
from app.web.deps import render
from app.web.routers.user import _capability_rows
from app.web.ws.board_hub import maybe_broadcast_for

logger = logging.getLogger("anni.web.capability")
router = APIRouter()


def _state(request: Request):
    return request.app.state.appstate


def _parse_conf(value: str | None, default: ConfidenceLevel) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(str(value).strip().lower())
    except (ValueError, AttributeError):
        return default


def _split_weapons(raw: str) -> list[str]:
    """Split the textarea (comma/newline separated) into trimmed names."""
    parts = re.split(r"[,\n]", raw or "")
    seen: list[str] = []
    for p in parts:
        name = p.strip()
        if name and name.lower() not in {s.lower() for s in seen}:
            seen.append(name)
    return seen


async def _capacity_fragment(request: Request, player, *, error: str | None = None,
                             flagged: list[str] | None = None) -> HTMLResponse:
    rows = await _capability_rows(player)
    taken = {r["role"] for r in rows}
    available = [r for r in capability_roles() if r not in taken]
    return render(
        request,
        "user/_capacity.html",
        capabilities=rows,
        is_core=cap_domain.is_core(len(rows)),
        fill_warning=None if rows else cap_domain.FILL_WARNING,
        available_roles=available,
        cap_error=error,
        cap_flagged=flagged or [],
    )


async def _require_user(request: Request):
    player = await auth.current_user(request)
    return player


@router.get("/me/capability/new", include_in_schema=False)
async def new_modal(request: Request, role: str = ""):
    """The add-capability modal: role picker + guidance + links."""
    player = await _require_user(request)
    if player is None:
        return RedirectResponse("/", status_code=303)
    rows = await _capability_rows(player)
    taken = {r["role"] for r in rows}
    chosen = parse_role(role)
    available = [r for r in capability_roles() if r not in taken]
    if chosen is None or chosen in taken:
        chosen = available[0] if available else None
    return render(
        request,
        "user/_capability_modal.html",
        mode="new",
        cap=None,
        role=chosen,
        guidance=guidance(chosen) if chosen else None,
        available_roles=available,
        max_weapons=MAX_WEAPONS_PER_CAPABILITY,
    )


@router.get("/me/capability/{cap_id}/edit", include_in_schema=False)
async def edit_modal(request: Request, cap_id: str):
    player = await _require_user(request)
    if player is None:
        return RedirectResponse("/", status_code=303)
    cap = (
        await RoleCapability.filter(id=cap_id, player=player)
        .prefetch_related("weapons")
        .first()
    )
    if cap is None:
        return await _capacity_fragment(request, player, error="Capability not found.")
    return render(
        request,
        "user/_capability_modal.html",
        mode="edit",
        cap={
            "id": str(cap.id),
            "role": cap.role,
            "confidence": cap.confidence,
            "build_quality": cap.build_quality,
            "success_count": cap.success_count,
            "weapons": ", ".join(w.weapon_name for w in cap.weapons),
        },
        role=cap.role,
        guidance=guidance(cap.role),
        available_roles=[cap.role],
        max_weapons=MAX_WEAPONS_PER_CAPABILITY,
    )


async def _write_weapons(
    request: Request, cap: RoleCapability, raw: str
) -> tuple[bool, str | None, list[str]]:
    """Validate + replace a capability's weapons. Returns (ok, error, flagged)."""
    names = _split_weapons(raw)
    if not cap_domain.weapons_within_cap(len(names)):
        return False, cap_domain.CAP_EXCEEDED, []
    catalog = _state(request).weapons_by_name
    resolved: list[tuple[str, str]] = []
    flagged: list[str] = []
    for name in names:
        res = cap_domain.validate_weapon(name, catalog)
        if res.check is cap_domain.WeaponCheck.INVALID:
            return False, (
                f"“{name}” isn't a recognised Wynncraft weapon. Check the "
                "spelling, or remove it."
            ), []
        if res.check is cap_domain.WeaponCheck.UNVERIFIED:
            flagged.append(name)
            resolved.append((name, "unverified"))
        else:
            resolved.append((name, res.subtype or "unknown"))
    await RoleCapabilityWeapon.filter(capability=cap).delete()
    for name, subtype in resolved:
        await RoleCapabilityWeapon.create(
            capability=cap, weapon_name=name, weapon_subtype=subtype
        )
    logger.debug(
        "weapons for cap %s: %d saved%s (catalog=%d entries)",
        cap.id, len(resolved),
        f", {len(flagged)} unverified" if flagged else "", len(catalog),
    )
    return True, None, flagged


@router.post("/me/capability", include_in_schema=False)
async def create_capability(
    request: Request,
    role: str = Form(...),
    confidence: str = Form("moderate"),
    build_quality: str = Form("moderate"),
    weapons: str = Form(""),
):
    player = await _require_user(request)
    if player is None:
        return RedirectResponse("/", status_code=303)
    parsed = parse_role(role)
    if parsed is None or parsed not in capability_roles():
        return await _capacity_fragment(request, player, error="Pick a core role.")
    if await RoleCapability.filter(player=player, role=parsed).exists():
        return await _capacity_fragment(
            request, player, error=f"You already have a {parsed.value} capability."
        )
    cap = await RoleCapability.create(
        player=player,
        role=parsed,
        confidence=_parse_conf(confidence, ConfidenceLevel.MODERATE),
        build_quality=_parse_conf(build_quality, ConfidenceLevel.MODERATE),
    )
    ok, err, flagged = await _write_weapons(request, cap, weapons)
    if not ok:
        await cap.delete()  # don't leave a weaponless half-created row
        logger.debug("capability create rejected for %s/%s: %s",
                     player.mc_username, parsed.value, err)
        return await _capacity_fragment(request, player, error=err)
    logger.info("capability added: %s -> %s", player.mc_username, parsed.value)
    await maybe_broadcast_for(player.mc_uuid)
    return await _capacity_fragment(request, player, flagged=flagged)


@router.post("/me/capability/{cap_id}", include_in_schema=False)
async def update_capability(
    request: Request,
    cap_id: str,
    confidence: str = Form("moderate"),
    build_quality: str = Form("moderate"),
    weapons: str = Form(""),
):
    player = await _require_user(request)
    if player is None:
        return RedirectResponse("/", status_code=303)
    cap = await RoleCapability.filter(id=cap_id, player=player).first()
    if cap is None:
        return await _capacity_fragment(request, player, error="Capability not found.")
    cap.confidence = _parse_conf(confidence, cap.confidence)
    cap.build_quality = _parse_conf(build_quality, cap.build_quality)
    await cap.save(update_fields=["confidence", "build_quality", "updated_at"])
    ok, err, flagged = await _write_weapons(request, cap, weapons)
    if not ok:
        return await _capacity_fragment(request, player, error=err)
    logger.info("capability updated: %s -> %s", player.mc_username, cap.role.value)
    await maybe_broadcast_for(player.mc_uuid)
    return await _capacity_fragment(request, player, flagged=flagged)


@router.post("/me/capability/{cap_id}/delete", include_in_schema=False)
async def delete_capability(request: Request, cap_id: str):
    player = await _require_user(request)
    if player is None:
        return RedirectResponse("/", status_code=303)
    deleted = await RoleCapability.filter(id=cap_id, player=player).delete()
    logger.info("capability deleted: %s (%s rows)", player.mc_username, deleted)
    if deleted:
        await maybe_broadcast_for(player.mc_uuid)
    return await _capacity_fragment(request, player)


@router.get("/me/capability/weapons", include_in_schema=False)
async def weapons_autocomplete(request: Request, q: str = ""):
    """Prefix match against the validated catalog (datalist options)."""
    if await _require_user(request) is None:
        return RedirectResponse("/", status_code=303)
    needle = q.strip().lower()
    catalog = _state(request).weapons_by_name
    matches: list[dict] = []
    if needle:
        for name_lower, subtype in catalog.items():
            if needle in name_lower:
                matches.append({"name": name_lower, "subtype": subtype})
            if len(matches) >= 12:
                break
    return render(request, "user/_weapon_options.html", matches=matches)
