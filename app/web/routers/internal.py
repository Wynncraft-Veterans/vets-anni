"""Internal endpoints reachable on the private ``verify`` Docker network.

Secret-gated via the shared ``DAZEBOT_INTROSPECT_SECRET`` (same value vets-anni
already uses for *outbound* calls to dazebot — reusing it inbound just means
both sides of the verify-network trust boundary share one rotation knob).

Today this exposes one read-only endpoint that dazebot's CTP link-bonus
reconciler polls to learn which Minecraft accounts have at least one
``RoleCapability`` row in fishbot. That gives dazebot the third 1-point
auto-award without dazebot needing access to vets-anni's database.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException

from app.db.models import RoleCapability
from app.settings import get_settings

logger = logging.getLogger("anni.web.internal")
router = APIRouter(prefix="/api/internal")


@router.get("/role-capability-uuids")
async def role_capability_uuids(
    x_introspect_secret: str | None = Header(default=None),
) -> dict[str, list[str]]:
    """Return every ``mc_uuid`` that has at least one ``RoleCapability`` row.

    Dazebot's CTP link-bonus reconciler intersects this with its own
    ``DiscordAccount.minecraft_account.uuid`` set to decide who's earned the
    fishbot-role bonus. Returns the bare list (no per-uuid role detail) —
    1 point is awarded for *any* declared capability and we don't want to
    encode anything more than presence/absence over the boundary.
    """
    expected = get_settings().dazebot_introspect_secret
    if not expected:
        logger.error(
            "role_capability_uuids: DAZEBOT_INTROSPECT_SECRET unset; refusing"
        )
        raise HTTPException(status_code=503, detail="internal endpoints disabled")
    if x_introspect_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")

    # ``RoleCapability.player_id`` is the AnniPlayer pk, which IS the
    # ``mc_uuid`` string (AnniPlayer.mc_uuid is ``primary_key=True``). One
    # player can have multiple capabilities (one per role) so we dedup in
    # Python — cheaper than a DISTINCT for the cardinality we expect.
    raw = await RoleCapability.all().values_list("player_id", flat=True)
    return {"uuids": sorted({str(u) for u in raw})}
