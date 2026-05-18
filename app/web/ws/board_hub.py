"""board_hub — the single source of truth for the organizer board.

Server-authoritative; clients are optimistic renderers. Every mutating intent
is applied **under one asyncio lock** (so ops are sequential — combined with
SQLite's single writer and ``buckets``'s ``(event,player)`` UPSERT this is the
third single-instance layer, ``.claude/data_model.md``), then the authoritative
state is broadcast so every tab — and the actor's own optimistic DOM —
converges. An *invalid* op (unknown player/party, ambiguous target, or the
grace freeze) is ``REJECTED`` and the client reverts; a *concurrent* op is not
rejected, it just applies next and the snapshot reconciles everyone.

Convergence model (deliberately the simplest correct one for a low-volume
staff tool, per ``.claude/ws_protocol.md``): after any successful mutation the
hub broadcasts a **full snapshot** as the PATCH payload; the fast/frequent
presence poller sends *granular* ``presence`` ops. ``HELLO`` (re)connect always
gets a fresh ``WELCOME`` snapshot — no fragile delta replay.

FastAPI-free on purpose: a "client" is anything with ``async send_text(str)``
(a Starlette ``WebSocket`` in prod, a tiny fake in tests), so the hub is unit-
testable and the poller→hub broadcast path needs no web import.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from app.constants import Role
from app.domain import buckets
from app.domain.schedule import is_board_frozen, phase_of
from app.services.state import AppState
from app.settings import get_settings
from app.web import board_view
from app.web import deps
from app.web.ws import protocol as P

logger = logging.getLogger("anni.ws.board")

_UNSET = ...  # "field not supplied" sentinel for partial PARTY_SET


class WSClient(Protocol):
    async def send_text(self, data: str) -> None: ...


def _grace_seconds() -> int:
    return max(0, get_settings().grace_hours) * 3600


class BoardHub:
    """Process-wide board hub (one per app; get via :func:`get_board_hub`)."""

    def __init__(self) -> None:
        self._clients: set[WSClient] = set()
        self._seq = 0
        self._lock = asyncio.Lock()

    # --- connection registry -------------------------------------------------
    def register(self, client: WSClient) -> None:
        self._clients.add(client)

    def unregister(self, client: WSClient) -> None:
        self._clients.discard(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def seq(self) -> int:
        return self._seq

    # --- low-level send ------------------------------------------------------
    async def _send(self, client: WSClient, frame: dict) -> bool:
        try:
            await client.send_text(deps.to_json(frame))
            return True
        except Exception:  # noqa: BLE001 - a dead socket must not break a tick
            logger.debug("ws send failed — dropping client", exc_info=True)
            self._clients.discard(client)
            return False

    async def _broadcast(self, frame: dict, *, skip: WSClient | None = None) -> None:
        for client in list(self._clients):
            if client is not skip:
                await self._send(client, frame)

    # --- snapshots / patches -------------------------------------------------
    async def send_welcome(self, client: WSClient, event, state: AppState) -> None:
        snap = await board_view.snapshot(event, state)
        await self._send(client, P.welcome(self._seq, snap))

    async def broadcast_snapshot(self, event, state: AppState) -> int:
        """Bump ``seq`` and push the full board to everyone (the post-mutation
        reconcile). Returns the new ``seq`` so the actor's ``APPLIED`` and the
        broadcast share it."""
        self._seq += 1
        snap = await board_view.snapshot(event, state)
        await self._broadcast(P.patch(self._seq, [{"op": "snapshot",
                                                   "snapshot": snap}]))
        return self._seq

    async def broadcast_patch(self, ops: list[dict]) -> None:
        """Granular deltas (the presence poller's status-border updates). No
        client = no-op (cheap early-out for the common idle board)."""
        if not ops or not self._clients:
            return
        self._seq += 1
        await self._broadcast(P.patch(self._seq, ops))

    async def broadcast_wipe(self, new_event: dict | None) -> None:
        self._seq += 1
        await self._broadcast(P.board_wipe(new_event))

    # --- intent handling -----------------------------------------------------
    async def handle(
        self, client: WSClient, intent: P.Intent, event, state: AppState
    ) -> None:
        """Apply one client intent (or answer HELLO/PING). All mutation runs
        under the hub lock so ops are strictly sequential."""
        if intent.type == P.HELLO:
            await self.send_welcome(client, event, state)
            return
        if intent.type == P.PING:
            await self._send(client, P.pong())
            return

        async with self._lock:
            phase = phase_of(event.stamp_epoch, _grace_seconds())
            frozen = is_board_frozen(phase)
            if frozen and intent.type != P.PARTY_SET:
                await self._send(client, P.rejected(
                    intent.op_id,
                    "The anni is in progress — the board is read-only "
                    "(only a party's result/stage can change now).",
                    self._seq,
                ))
                return

            result = await self._apply(intent, event, state, frozen=frozen)

            if not result.ok:
                await self._send(client, P.rejected(
                    intent.op_id, result.reason or "Rejected.", self._seq))
                logger.debug("ws %s rejected: %s", intent.type, result.reason)
                return

        # Lock released: ack the actor, then reconcile every tab (incl. the
        # actor — the snapshot supersedes their optimistic DOM).
        seq = await self.broadcast_snapshot(event, state)
        await self._send(client, P.applied(intent.op_id, seq))
        logger.debug("ws %s applied -> seq %d (%d clients)",
                     intent.type, seq, len(self._clients))

    async def _apply(
        self, intent: P.Intent, event, state: AppState, *, frozen: bool
    ) -> buckets.OpResult:
        d: dict[str, Any] = intent.data

        if intent.type == P.PLAYER_ADD:
            # Walk-in by IGN: cache-first resolve + get-or-create + UPSERT into
            # Unassigned; idempotent for an on-board player (buckets enforces
            # it). Unknown IGN -> friendly REJECTED.
            return await buckets.add_walkin(
                event, str(d.get("ign", "")), state
            )

        if intent.type == P.MOVE:
            target = d.get("target") or {}
            return await buckets.move(
                event,
                str(d.get("player_uuid", "")),
                bucket=_parse_bucket(target.get("bucket")),
                party_id=target.get("party_id"),
                sort_index=int(target.get("sort_index", 0) or 0),
                is_late=target.get("is_late"),
            )

        if intent.type == P.ASSIGN_ROLE:
            return await buckets.assign_role(
                event, str(d.get("player_uuid", "")),
                _parse_role(d.get("role")),
            )

        if intent.type == P.PARTY_CREATE:
            await buckets.create_party(event)
            return buckets.OpResult(True)

        if intent.type == P.PARTY_RENAME:
            return await buckets.rename_party(
                event, str(d.get("party_id", "")),
                int(d.get("ordinal", 0) or 0),
            )

        if intent.type == P.PARTY_SET:
            # During grace ONLY result + stage may change; host/world are
            # frozen out by passing the "untouched" sentinel.
            return await buckets.set_party(
                event,
                str(d.get("party_id", "")),
                host_uuid=_UNSET if frozen else _opt(d, "host_uuid"),
                world=_UNSET if frozen else _opt(d, "world"),
                stage=_opt(d, "stage"),
                result=_opt(d, "result"),
            )

        if intent.type == P.ORGANIZER_SET:
            return await buckets.set_organizer(event, d.get("player_uuid"))

        return buckets.OpResult(False, "Unknown intent.")


def _opt(d: dict, key: str):
    """Present (incl. explicit ``null``) → the value; absent → the
    ``set_party`` "leave untouched" sentinel."""
    return d[key] if key in d else _UNSET


def _parse_bucket(value: Any):
    from app.constants import BucketKind

    if not value:
        return None
    try:
        return BucketKind(str(value).strip().lower())
    except ValueError:
        return None


def _parse_role(value: Any) -> Role | None:
    if not value:
        return None
    try:
        return Role(str(value).strip().lower())
    except ValueError:
        return None


_hub: BoardHub | None = None


def get_board_hub() -> BoardHub:
    """Process-wide board hub singleton (lazily constructed, like the WAPI/
    tempserver clients) so pollers and the WS route share one instance."""
    global _hub
    if _hub is None:
        _hub = BoardHub()
    return _hub
