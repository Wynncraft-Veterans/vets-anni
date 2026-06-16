"""Internal MWE (Major World Event) snapshot endpoints — verify-network only.

Mirror the secret-gating pattern in :mod:`app.web.routers.internal`: a shared
``X-Introspect-Secret`` header, fail-closed when unset. The consumer is
temporary-server's :mod:`app.services.anni_snapshot_poller`, which fans the
data out to vetsmod clients as ``anni_state`` WS frames.

Endpoints (S1):

* ``GET  /api/internal/anni-eligibility`` — every UUID the poller should
  fetch (the "plausible vets-anni users" set: anyone with an
  :class:`AnniPlayer` row).
* ``GET  /api/internal/anni-player/{uuid}`` — one fresh snapshot for a
  specific UUID. Serves the on-demand pull (vetsmod ``anni_query``).
* ``POST /api/internal/anni-snapshot-batch`` — batched snapshots for the
  poller's regular tick. Body: ``{"uuids":[...]}``.

Hard architectural rule #2: every endpoint returns the SAME shape produced
by :func:`app.domain.snapshot.assemble_snapshot`. Don't add per-endpoint
variants — the vetsmod side treats this as opaque transit.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer
from app.domain.snapshot import (
    assemble_snapshot,
    assemble_snapshot_for_uuid,
    push_eligible_uuids,
)
from app.services.state import AppState
from app.settings import get_settings

logger = logging.getLogger("anni.web.anni_internal")
router = APIRouter(prefix="/api/internal")


def _check_secret(x_introspect_secret: str | None) -> None:
    """Fail-closed shared-secret check. Mirrors ``internal.py``."""
    expected = get_settings().anni_introspect_secret
    if not expected:
        logger.error(
            "anni_internal: ANNI_INTROSPECT_SECRET unset; refusing"
        )
        raise HTTPException(
            status_code=503, detail="internal endpoints disabled"
        )
    if x_introspect_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def _state(request: Request) -> AppState:
    return request.app.state.appstate


@router.get("/anni-eligibility")
async def anni_eligibility(
    x_introspect_secret: str | None = Header(default=None),
) -> dict[str, list[str]]:
    """Every UUID temporary-server should poll snapshots for.

    Returned as ``{"uuids": [...]}`` so the wire shape can grow extra
    metadata (e.g. per-uuid hot-window hints) without a breaking version
    bump.
    """
    _check_secret(x_introspect_secret)
    return {"uuids": await push_eligible_uuids()}


@router.get("/anni-player/{uuid}")
async def anni_player(
    request: Request,
    uuid: str,
    x_introspect_secret: str | None = Header(default=None),
) -> dict:
    """Synchronous fresh snapshot for one player.

    404 when the player isn't in the DB (the row is the registration
    check — no row, no snapshot). vetsmod treats the 404 as "this user
    has no enriched view available" and falls back to the legacy
    ``/wv anni`` rendering.
    """
    _check_secret(x_introspect_secret)
    snapshot = await assemble_snapshot_for_uuid(uuid, _state(request))
    if snapshot is None:
        raise HTTPException(status_code=404, detail="player not found")
    return snapshot


@router.post("/anni-snapshot-batch")
async def anni_snapshot_batch(
    request: Request,
    payload: dict,
    x_introspect_secret: str | None = Header(default=None),
) -> dict:
    """Batched snapshots: body ``{"uuids":[...]}`` -> ``{"snapshots":[...]}``.

    The poller's regular tick. Implementation deliberately reuses the
    single-player path under one ``get_active_event()`` lookup so a batch
    of N still costs 1 event read + N player reads instead of 2N.
    Missing UUIDs are simply absent from the response (no error per UUID —
    eligibility can briefly diverge between poller cache and DB and we
    don't want a single stale UUID to fail the whole batch).
    """
    _check_secret(x_introspect_secret)
    uuids_raw = payload.get("uuids") if isinstance(payload, dict) else None
    if not isinstance(uuids_raw, list):
        raise HTTPException(
            status_code=400, detail="body must be {\"uuids\": [...]}"
        )
    uuids = [str(u) for u in uuids_raw if u]

    if not uuids:
        return {"snapshots": []}

    event = await get_active_event()
    state = _state(request)

    players = await AnniPlayer.filter(mc_uuid__in=uuids).all()
    snapshots: list[dict] = []
    for player in players:
        try:
            snapshots.append(await assemble_snapshot(player, event, state))
        except Exception:
            logger.exception(
                "anni_snapshot_batch: failed for uuid=%s", player.mc_uuid
            )
            # Skip — partial batch is better than a 500 for the whole tick.
            continue
    return {"snapshots": snapshots}
