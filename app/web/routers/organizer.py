"""App3 — the organizer drag-drop board: SSR page + WebSocket + REST twins.

* ``GET /staff/board`` — parties (left) / buckets (right) / legend + CB toggle
  + info + an add-user-by-IGN control. Rendered server-side from the *same*
  ``board_view.snapshot`` the socket ships, so a refresh is byte-identical to
  the live state and the board still works with JS off.
* ``WS /staff/board/ws`` — drives ``board_hub`` (server-authoritative).
* ``POST /staff/board/*`` — a REST twin for **every** mutation. board.js
  prefers the socket and falls back to these (HTMX) if it drops, so the tool
  degrades instead of dying. Each twin runs the *same* ``board_hub.handle``
  path (so WS tabs stay in sync) and returns the refreshed ``#board`` fragment.

Staff-gated everywhere (the WS handshake re-checks the signed cookie — the
low-trust model still gates every mutation behind the staff session).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

from app.constants import PartyResult
from app.db.lifecycle import get_active_event
from app.domain.roles import assignable_roles
from app.web import auth, board_view, deps
from app.web.ws import protocol as P
from app.web.ws.board_hub import get_board_hub

logger = logging.getLogger("anni.web.organizer")
router = APIRouter()


def _state(request: Request):
    return request.app.state.appstate


def _is_staff_conn(conn) -> bool:
    """Staff check for an HTTP *or* WS connection (both expose ``.cookies``;
    ``deps.read_session`` only reads that)."""
    return deps.read_session(conn).get("kind") == "staff"


async def _board_ctx(request: Request) -> dict:
    event = await get_active_event()
    snap = None
    if event is not None:
        snap = await board_view.snapshot(event, _state(request))
    return {
        "snapshot": snap,
        "has_event": event is not None,
        "assignable_roles": list(assignable_roles()),
        "results": [r.value for r in PartyResult],
    }


@router.get("/staff/board")
async def board_page(request: Request):
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    return render_board(request, await _board_ctx(request), full=True)


@router.get("/staff/board/add", include_in_schema=False)
async def board_add_modal(request: Request):
    """The add-a-walk-in popup (HTMX-mounted into #board-modal-mount). The
    form itself posts the existing player-add REST twin."""
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    return deps.render(request, "staff/_add_modal.html")


@router.get("/staff/board/fragment", include_in_schema=False)
async def board_fragment(request: Request):
    """Just the ``#board`` block. board.js re-fetches this whenever the socket
    signals a change (WELCOME/PATCH/BOARD_WIPE) — so all rendering stays
    server-side (one ``board_view``), no client-side templating to drift."""
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    return render_board(request, await _board_ctx(request), full=False)


def render_board(request: Request, ctx: dict, *, full: bool,
                 error: str | None = None) -> HTMLResponse:
    """Full page (initial load) or just the ``#board`` fragment (HTMX twin)."""
    template = "staff/board.html" if full else "staff/_board.html"
    return deps.render(request, template, error=error, **ctx)


# --- WebSocket -------------------------------------------------------------
@router.websocket("/staff/board/ws")
async def board_ws(websocket: WebSocket):
    if not _is_staff_conn(websocket):
        await websocket.close(code=4403)  # policy violation: not staff
        return
    await websocket.accept()
    hub = get_board_hub()
    state = websocket.app.state.appstate
    hub.register(websocket)
    logger.info("board ws connected (%d clients)", hub.client_count)
    try:
        event = await get_active_event()
        if event is None:
            await websocket.send_text(deps.to_json(P.board_wipe(None)))
        else:
            await hub.send_welcome(websocket, event, state)
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                continue  # ignore garbage; never kill the reader
            intent = P.parse_intent(frame)
            if intent is None:
                continue
            event = await get_active_event()  # re-read: wipe/re-announce safe
            if event is None:
                await websocket.send_text(deps.to_json(P.board_wipe(None)))
                continue
            await hub.handle(websocket, intent, event, state)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a bad socket must not bubble out
        logger.warning("board ws errored — closing", exc_info=True)
    finally:
        hub.unregister(websocket)
        logger.info("board ws disconnected (%d clients)", hub.client_count)


# --- REST twins (HTMX fallback for a dropped socket) -----------------------
class _Recorder:
    """A fake hub client that just captures frames, so the REST twins reuse
    the *exact* ``board_hub.handle`` path (grace gate, single-instance UPSERT,
    WS broadcast to live tabs) instead of a divergent second code path."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.frames.append(json.loads(data))


async def _apply_rest(request: Request, intent: P.Intent) -> HTMLResponse:
    if not auth.is_staff(request):
        return RedirectResponse("/staff", status_code=303)
    state = _state(request)
    event = await get_active_event()
    if event is None:
        return render_board(request, await _board_ctx(request), full=False,
                            error="No anni is announced.")
    rec = _Recorder()
    await get_board_hub().handle(rec, intent, event, state)
    reason = next(
        (f.get("reason") for f in rec.frames if f.get("type") == P.REJECTED),
        None,
    )
    return render_board(request, await _board_ctx(request), full=False,
                        error=reason)


@router.post("/staff/board/player-add", include_in_schema=False)
async def rest_player_add(request: Request, ign: str = Form("")):
    """Add a walk-in by IGN (idempotent — re-adding an on-board player is a
    no-op; the friendly reason surfaces inline for an unknown IGN). Same
    ``board_hub`` path as the WS ``PLAYER_ADD`` so live tabs stay in sync."""
    return await _apply_rest(request, P.Intent(P.PLAYER_ADD,
                                               data={"ign": ign}))


@router.post("/staff/board/move", include_in_schema=False)
async def rest_move(
    request: Request,
    player_uuid: str = Form(...),
    bucket: str = Form(""),
    party_id: str = Form(""),
    sort_index: int = Form(0),
    is_late: bool = Form(False),
):
    target: dict = {"sort_index": sort_index, "is_late": is_late}
    if party_id:
        target["party_id"] = party_id
    else:
        target["bucket"] = bucket
    return await _apply_rest(request, P.Intent(
        P.MOVE, data={"player_uuid": player_uuid, "target": target}))


@router.post("/staff/board/assign-role", include_in_schema=False)
async def rest_assign_role(
    request: Request,
    player_uuid: str = Form(...),
    role: str = Form(""),
):
    return await _apply_rest(request, P.Intent(
        P.ASSIGN_ROLE, data={"player_uuid": player_uuid, "role": role or None}))


@router.post("/staff/board/party/create", include_in_schema=False)
async def rest_party_create(request: Request):
    return await _apply_rest(request, P.Intent(P.PARTY_CREATE))


@router.post("/staff/board/party/{party_id}/set", include_in_schema=False)
async def rest_party_set(
    request: Request,
    party_id: str,
    host_uuid: str | None = Form(None),
    world: str | None = Form(None),
    stage: int | None = Form(None),
    result: str | None = Form(None),
):
    data: dict = {"party_id": party_id}
    # Only forward fields the form actually submitted (so grace's
    # result/stage-only rule and partial edits work via the same sentinel).
    if host_uuid is not None:
        data["host_uuid"] = host_uuid or None
    if world is not None:
        data["world"] = world
    if stage is not None:
        data["stage"] = stage
    if result is not None:
        data["result"] = result
    return await _apply_rest(request, P.Intent(P.PARTY_SET, data=data))


@router.post("/staff/board/party/{party_id}/rename", include_in_schema=False)
async def rest_party_rename(
    request: Request, party_id: str, ordinal: int = Form(...)
):
    return await _apply_rest(request, P.Intent(
        P.PARTY_RENAME, data={"party_id": party_id, "ordinal": ordinal}))


@router.post("/staff/board/organizer", include_in_schema=False)
async def rest_organizer(request: Request, player_uuid: str = Form("")):
    return await _apply_rest(request, P.Intent(
        P.ORGANIZER_SET, data={"player_uuid": player_uuid or None}))
