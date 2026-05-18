"""Board WebSocket wire protocol — frame parsing + building (pure).

JSON frames, ``{"v": 1, "type": ..., ...}`` (see ``.claude/ws_protocol.md``).
No FastAPI/Tortoise import here: this is just the shape, so it is trivially
unit-testable and the hub stays the only place that touches the DB.

Client → server intents are parsed into one tolerant :class:`Intent` (an
unknown/garbled frame → ``None``, never an exception that could kill the socket
reader). ``op_id`` is the client's optimistic-change id, echoed back in
``APPLIED``/``REJECTED`` so the client knows which DOM change to confirm/revert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

# Client → server.
HELLO = "HELLO"
PLAYER_ADD = "PLAYER_ADD"
MOVE = "MOVE"
ASSIGN_ROLE = "ASSIGN_ROLE"
PARTY_CREATE = "PARTY_CREATE"
PARTY_RENAME = "PARTY_RENAME"
PARTY_SET = "PARTY_SET"
ORGANIZER_SET = "ORGANIZER_SET"
PING = "PING"

#: Intents that mutate the board (everything bar the connection/keepalive
#: pair). The hub gates these during grace.
MUTATING = frozenset(
    {PLAYER_ADD, MOVE, ASSIGN_ROLE, PARTY_CREATE, PARTY_RENAME, PARTY_SET,
     ORGANIZER_SET}
)

# Server → client.
WELCOME = "WELCOME"
APPLIED = "APPLIED"
REJECTED = "REJECTED"
PATCH = "PATCH"
BOARD_WIPE = "BOARD_WIPE"
PONG = "PONG"

_CLIENT_TYPES = frozenset(
    {HELLO, PLAYER_ADD, MOVE, ASSIGN_ROLE, PARTY_CREATE, PARTY_RENAME,
     PARTY_SET, ORGANIZER_SET, PING}
)


@dataclass(frozen=True)
class Intent:
    """A parsed client frame. ``data`` is the raw frame minus envelope keys;
    the hub reads only the fields each ``type`` defines (and tolerates missing
    ones — a bad value becomes a friendly ``REJECTED``, not a crash)."""

    type: str
    op_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def parse_intent(raw: Any) -> Intent | None:
    """A decoded JSON frame → :class:`Intent`, or ``None`` if it isn't a
    recognised client intent. Deliberately permissive about *fields* (the hub
    validates those and replies ``REJECTED``); strict only about *shape*."""
    if not isinstance(raw, dict):
        return None
    typ = raw.get("type")
    if typ not in _CLIENT_TYPES:
        return None
    op_id = raw.get("op_id")
    data = {k: v for k, v in raw.items() if k not in ("v", "type", "op_id")}
    return Intent(type=typ, op_id=str(op_id) if op_id is not None else None,
                  data=data)


def _frame(type_: str, **fields: Any) -> dict:
    return {"v": PROTOCOL_VERSION, "type": type_, **fields}


def welcome(seq: int, snapshot: dict) -> dict:
    return _frame(WELCOME, seq=seq, snapshot=snapshot)


def applied(op_id: str | None, seq: int) -> dict:
    return _frame(APPLIED, op_id=op_id, seq=seq)


def rejected(op_id: str | None, reason: str, seq: int) -> dict:
    return _frame(REJECTED, op_id=op_id, reason=reason, seq=seq)


def patch(seq: int, ops: list[dict]) -> dict:
    return _frame(PATCH, seq=seq, ops=ops)


def board_wipe(new_event: dict | None = None) -> dict:
    return _frame(BOARD_WIPE, new_event=new_event)


def pong() -> dict:
    return _frame(PONG)
