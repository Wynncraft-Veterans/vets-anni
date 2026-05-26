# Organizer-board WebSocket protocol

`WS /staff/board/ws` (staff only). `app/web/ws/board_hub.py` is the single
source of truth (server-authoritative); clients are optimistic renderers.
Frames are JSON `{v:1, type, ...}`.

## Client → server (intents)
- `HELLO {since_seq?}` — connect/resume.
- `MOVE {player_uuid, target:{bucket|party_id, sort_index}, op_id}`
- `ASSIGN_ROLE {player_uuid, role|null, op_id}`
- `PARTY_CREATE {}` · `PARTY_RENAME {party_id, ordinal}`
- `PARTY_DELETE {party_id}` — empty parties only; the hub rejects non-empty
  with a friendly reason (the UI hides the button on non-empty cards, so this
  is a safety net for hand-crafted requests).
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
- **Convergence (impl):** after any successful mutation the hub broadcasts a
  **full snapshot** as the single `PATCH` op (`{op:"snapshot",snapshot}`) —
  the simplest correct model for a low-volume tool; the actor also gets
  `APPLIED{op_id,seq}` (same seq). The *presence poller* sends granular
  `{op:"presence",player_uuid,status,chip}` ops. `board.js` never templates:
  any `WELCOME`/`PATCH`/`BOARD_WIPE` just re-fetches the SSR `#board`
  fragment, so SSR and live render through one path and can't drift.
- **Degradation:** every WS mutation has a `POST /staff/board/*` REST twin so
  the board still works if the socket drops (HTMX fallback). The twins run
  the *same* `board_hub.handle` (a recorder stands in for the socket), so a
  no-JS action still broadcasts to live tabs and keeps the single-instance
  guarantee — no divergent second code path.
- During grace (now>stamp & ≤stamp+2h) the board is read-only except
  `PARTY_SET {result, stage}` — enforced live in `board_hub` via
  `domain/schedule.phase_of` (not a stored flag, so clock skew can't strand
  it). `PLAYER_ADD` re-adding an on-board player is an idempotent no-op.
- **Testing:** the hub is FastAPI-free, so it is unit-tested directly with a
  fake client (seq monotonicity, grace gate, single-instance, idempotency) —
  not over a real socket (the httpx ASGITransport test transport has no
  websocket/lifespan by design).
