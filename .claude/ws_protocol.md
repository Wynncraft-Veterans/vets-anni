# Organizer-board WebSocket protocol

`WS /staff/board/ws` (staff only). `app/web/ws/board_hub.py` is the single
source of truth (server-authoritative); clients are optimistic renderers.
Frames are JSON `{v:1, type, ...}`.

## Client → server (intents)
- `HELLO {since_seq?}` — connect/resume.
- `MOVE {player_uuid, target:{bucket|party_id, sort_index}, op_id}`
- `ASSIGN_ROLE {player_uuid, role|null, op_id}`
- `PARTY_CREATE {}` · `PARTY_RENAME {party_id, ordinal}`
- `PARTY_SET {party_id, host_uuid?, world?, stage?, result?, op_id}`
- `ORGANIZER_SET {player_uuid|null}` · `PING`

## Server → client
- `WELCOME {seq, snapshot:{event, parties[], buckets{...}, placements[]}}`
- `APPLIED {op_id, seq}` — commit ack.
- `REJECTED {op_id, reason, seq}` — client reverts the optimistic change.
- `PATCH {seq, ops:[...]}` — authoritative deltas (other staff + presence
  poller status-border updates).
- `BOARD_WIPE {new_event?}` — grace-wipe; clients clear + resnapshot.
- `PONG`

## Guarantees
- **Single-instance:** every `MOVE` validated + applied in a DB transaction
  that UPSERTs the unique `(event, player)` `BoardPlacement` row. Moving out of
  a container is implicit (row updated, never duplicated).
- **Ordering:** `board_hub` holds a monotonic `seq`; ops applied sequentially
  on the event loop (SQLite single writer). Reconnect → `HELLO{since_seq}` →
  fresh `WELCOME` snapshot (simplest correct behaviour for a low-volume staff
  tool; no fragile delta replay).
- **Degradation:** every WS mutation has a `POST /staff/board/*` REST twin so
  the board still works if the socket drops (HTMX fallback).
- During grace (now>stamp & ≤stamp+2h) the board is read-only except
  `PARTY_SET {result, stage}`.
