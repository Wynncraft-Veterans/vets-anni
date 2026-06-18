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

S5:

* ``POST /api/internal/anni-party-scrollspot`` — host of a party writes
  (or clears) the in-game "scroll spot" coordinate. Body:
  ``{"actor_mc_uuid":"...","scroll_spot":{"x","y","z"}|null}``. The
  ``actor_mc_uuid`` is forwarded by temporary-server from the authenticated
  session; this endpoint then verifies the actor is the host of their
  currently-assigned party in the active event.

S6:

* ``POST /api/internal/anni-rsvp-by-uuid`` — authenticated vetsmod users
  RSVP from in-game. Body: ``{"actor_mc_uuid":"...","notice":"hard"|"soft"|"revoke"}``.
  Forwards to :func:`app.domain.rsvp_by_uuid.execute_uuid_rsvp` which
  reuses the cog's ``set_rsvp`` / ``revoke`` / auto-place / broadcast /
  public-post chain, so a ``/wv anni rsvp hard`` is byte-equivalent to a
  Discord ``\\rsvp hard``.

S7:

* ``POST /api/internal/anni-party-observation`` — authenticated vetsmod
  client reports its local Wynncraft party roster when an organiser
  username appears in the party. Body:
  ``{"observer_mc_uuid","party_member_usernames":[...],"leader_username","world"}``.
  ``observer_mc_uuid`` is stamped by temporary-server from the
  authenticated session (never trusted from the frame body). Names are
  resolved here via :meth:`AppState.resolve_uuid` (roster scan → legacy
  alias fallback) and written into ``state.party_leader_by_uuid`` for the
  presence classifier's ``ONLINE_PARTY`` upgrade.

Hard architectural rule #2: every endpoint returns the SAME shape produced
by :func:`app.domain.snapshot.assemble_snapshot`. Don't add per-endpoint
variants — the vetsmod side treats this as opaque transit.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Header, HTTPException, Request

from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer, BoardPlacement
from app.domain.rsvp_by_uuid import UuidRsvpError, execute_uuid_rsvp
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


@router.post("/anni-party-scrollspot")
async def anni_party_scrollspot(
    payload: dict,
    x_introspect_secret: str | None = Header(default=None),
) -> dict:
    """Host of a party writes (or clears) the party's scroll-spot coord.

    The trust chain: vetsmod → temporary-server (authenticated WS session) →
    here. temporary-server forwards the session's MC UUID as ``actor_mc_uuid``;
    we look up that UUID's party in the active event and accept the write
    iff the actor IS that party's :attr:`Party.host`. No impersonation
    possible — the client cannot supply an arbitrary UUID; temp-server
    sets it from the auth session.

    Body shape::

        {
          "actor_mc_uuid": "deadbeef-...",
          "scroll_spot": {"x": 345, "y": 45, "z": -1315}    // or null to clear
        }

    Cleared automatically at grace-wipe (see ``lifecycle_task._wipe``).
    """
    _check_secret(x_introspect_secret)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    actor_mc_uuid = payload.get("actor_mc_uuid")
    if not isinstance(actor_mc_uuid, str) or not actor_mc_uuid:
        raise HTTPException(
            status_code=400, detail="actor_mc_uuid required"
        )

    spot = payload.get("scroll_spot")
    if spot is not None:
        if (
            not isinstance(spot, dict)
            or not all(isinstance(spot.get(k), int) for k in ("x", "y", "z"))
        ):
            raise HTTPException(
                status_code=400,
                detail="scroll_spot must be null or {x:int, y:int, z:int}",
            )

    placement = (
        await BoardPlacement.filter(
            event__is_active=True,
            player__mc_uuid=actor_mc_uuid,
            party__isnull=False,
        )
        .select_related("party__host")
        .first()
    )
    if placement is None or placement.party is None:
        raise HTTPException(
            status_code=403,
            detail="actor is not in a party for the active event",
        )
    party = placement.party
    if party.host is None or party.host.mc_uuid != actor_mc_uuid:
        raise HTTPException(
            status_code=403, detail="only the party host can set scroll_spot"
        )

    if spot is None:
        party.scroll_spot_x = None
        party.scroll_spot_y = None
        party.scroll_spot_z = None
    else:
        party.scroll_spot_x = spot["x"]
        party.scroll_spot_y = spot["y"]
        party.scroll_spot_z = spot["z"]
    await party.save(
        update_fields=["scroll_spot_x", "scroll_spot_y", "scroll_spot_z"]
    )
    return {"status": "ok"}


@router.post("/anni-rsvp-by-uuid")
async def anni_rsvp_by_uuid(
    request: Request,
    payload: dict,
    x_introspect_secret: str | None = Header(default=None),
) -> dict:
    """In-game RSVP entrypoint — temp-server forwards from authenticated WS.

    Trust chain: vetsmod -> temp-server (authenticated session stamps
    ``actor_mc_uuid``) -> here. The same downstream as the Discord cog so
    the Rsvp row, auto-placement, board-snapshot broadcast, and public
    confirmation post are byte-identical between the two surfaces.

    Body shape::

        {"actor_mc_uuid": "...", "notice": "hard" | "soft" | "revoke"}

    Returns ``{"status": "ok"}`` on success. The four refusable cases
    (no active event, invalid notice, missing UUID, T-90 cutoff) map to
    4xx with ``{"status":"error","detail":"..."}``.
    """
    _check_secret(x_introspect_secret)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    actor_mc_uuid = payload.get("actor_mc_uuid")
    if not isinstance(actor_mc_uuid, str) or not actor_mc_uuid:
        raise HTTPException(status_code=400, detail="actor_mc_uuid required")

    notice = payload.get("notice")
    if notice not in ("hard", "soft", "revoke"):
        raise HTTPException(
            status_code=400,
            detail='notice must be "hard", "soft", or "revoke"',
        )

    bot = getattr(request.app.state, "fishbot", None)
    try:
        await execute_uuid_rsvp(bot, actor_mc_uuid, notice)
    except UuidRsvpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"status": "ok"}


@router.post("/anni-party-observation")
async def anni_party_observation(
    request: Request,
    payload: dict,
    x_introspect_secret: str | None = Header(default=None),
) -> dict:
    """S7 vetsmod back-report — record a Wynncraft party roster observation.

    Body shape::

        {
          "observer_mc_uuid": "<uuid>",
          "party_member_usernames": ["foo", "bar"],
          "leader_username": "foo",
          "world": "WC1"
        }

    ``observer_mc_uuid`` is injected by temporary-server from the
    authenticated session; the frame body's value is the same (passed
    through) but the *trust* root is the session.

    Resolution: ``leader_username`` and each member name go through
    :meth:`AppState.resolve_uuid` (cached roster, then legacy-name
    aliases). Unresolvable names are dropped; an unresolvable leader
    short-circuits the whole observation (no anchor for the
    ``ONLINE_PARTY`` upgrade). The observer's session UUID is the
    authoritative fallback for the observer's own entry — even if their
    username didn't resolve through the cache, we still record them.

    Returns ``{"status": "ok", "resolved": <int>, "dropped": <int>}`` for
    observability. The poller doesn't act on the counts; they exist for
    debug-surface inspection.
    """
    _check_secret(x_introspect_secret)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    observer_mc_uuid = payload.get("observer_mc_uuid")
    if not isinstance(observer_mc_uuid, str) or not observer_mc_uuid:
        raise HTTPException(status_code=400, detail="observer_mc_uuid required")

    raw_members = payload.get("party_member_usernames")
    if not isinstance(raw_members, list) or not all(
        isinstance(m, str) for m in raw_members
    ):
        raise HTTPException(
            status_code=400,
            detail="party_member_usernames must be a list of strings",
        )

    leader_username = payload.get("leader_username")
    if not isinstance(leader_username, str):
        raise HTTPException(
            status_code=400, detail="leader_username must be a string"
        )

    world = payload.get("world")
    if not isinstance(world, str):
        raise HTTPException(status_code=400, detail="world must be a string")

    # Dedupe member names case-insensitively, strip blanks. Order doesn't
    # matter — the dict mutation is set-like on UUID.
    seen_lower: set[str] = set()
    deduped: list[str] = []
    for raw in raw_members:
        norm = raw.strip()
        if not norm:
            continue
        key = norm.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        deduped.append(norm)

    state = _state(request)

    leader_uuid: str | None = (
        state.resolve_uuid(leader_username) if leader_username else None
    )
    if not leader_uuid:
        # No anchor → corroboration can't fire; treat as a clean no-op so
        # the caller still sees an OK ack.
        return {"status": "ok", "resolved": 0, "dropped": len(deduped)}

    resolved: dict[str, str] = {}
    dropped = 0
    for name in deduped:
        uuid = state.resolve_uuid(name)
        if uuid:
            resolved[uuid] = leader_uuid
        else:
            dropped += 1

    # Authoritative fallback: the observer's session UUID always maps to
    # the leader, even if their username didn't resolve through the cache
    # (e.g. a brand-new member whose username row hasn't ingested yet).
    resolved.setdefault(observer_mc_uuid, leader_uuid)

    state.party_leader_by_uuid.update(resolved)
    state.party_observation_fetched_at = time.time()

    return {"status": "ok", "resolved": len(resolved), "dropped": dropped}
