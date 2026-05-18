# vets-anni

Wynnvets **Annihilation** coordination stack — consolidates the fragmented
tools used to run the 10-player anni event into one app: a user dashboard
(App1), a Discord bot **fishbot** (`/rsvp`, App2), a staff/organizer
drag-and-drop board (App3), and later vetsmod in-game integration (App4).

Deploys as `anni.wynnvets.org` on the vets-deploy "timasca" VPS, serviced by
`manage`. One lean Python process: FastAPI + fishbot + background pollers on a
single asyncio loop.

## Documentation hub

| Doc | What |
|-----|------|
| [spec.md](spec.md) | **Authoritative design spec** for this application. |
| [architecture.md](architecture.md) | Process model, stack, package map, data flow. |
| [data_model.md](data_model.md) | Tortoise schema, the single-instance invariant, Aerich migrations. |
| [domain_rules.md](domain_rules.md) | Roles/colours, membership, attendance table, presence state machine, lifecycle/grace-wipe. |
| [integration.md](integration.md) | Contracts with temporary-server, dazebot, vetsmod; OWN WAPI token; low-trust auth. |
| [ws_protocol.md](ws_protocol.md) | The organizer-board WebSocket protocol + single-writer model. |
| [colourblind.md](colourblind.md) | The mandatory colourblind variant mechanism. |
| [deployment.md](deployment.md) | The vets-deploy stack, env, build, `manage`, local dev (no `uv`). |

These docs are the source of truth — keep them current as the build
progresses. Phasing/status is at the bottom of this file.

## Hard rules (do not violate)

- **Identity anchor is the Minecraft UUID.** Never key anything on username.
- **vets-anni uses its OWN WAPI token** (separate ratelimit bucket — mandated).
  Never reuse the dazebot/temporary-server token.
- **Online truth = mirror vetsmod `/wv list`** (merge `api.wynnvets.org`
  `/v1/outbound/{list,roster,aliases}` + WAPI guild online + grace cache).
  Never trust the bare Wynncraft server API alone.
  - **In-queue players are not offline** (see the queue state in the above)
- **Auth is intentionally low-trust** (IGN + optional password) — a
  coordination tool, not a security boundary. Documented in integration.md.
- **Colourblind variant is mandatory on every interface** — colour is never
  the only signal (glyph + label + border pattern always accompany it).
- **Single-instance-per-person** on the board: DB `unique_together(event,
  player)` on `BoardPlacement`; every move is an UPSERT in a transaction.
- **API-disabled users** (epoch `last_online`): infer via the online-merge /
  purgelist heuristic; if unconfirmable show **"unknown"** — never fabricate
  online.
- `/rsvp` replies to the user **ephemerally** but also posts a concise
  **public** confirmation line to `RSVP_CHANNEL_ID` (a record/visibility ack).
- One cross-repo addition to dazebot only: secret-gated
  `POST /api/internal/anni-identity` (reuses `verify_keys.resolve_tier`).
- Durable docs live in `.claude/*.md` (indexed above) and stay in version
  control; link new ones here. Use `.claude/ephemeral` for temporary work.
- Tasteful comments throughout; every package has a one-responsibility
  docstring. Modular & migratable — future expansion is expected.

## Status

Build in progress, phased: **0** ✅ skeleton+deploy → **1** ✅ App1 (user
web) → **2** ✅ App3 (staff/board) → **3** App2 (fishbot) → **4** App4
(vetsmod, deferred & coordinated). See the plan file for per-phase scope +
verification.

**Phase 2 done (2026-05-18):** the staff/organizer board.
`domain/schedule.py` (pure event-phase: PENDING/GRACE/EXPIRED) +
`domain/buckets.py` (the **sole** `BoardPlacement` writer — UPSERT-in-
transaction; `move`/`assign_role`/`add_walkin`/party+organiser ops + raw
`board_rows`). `web/board_view.py` = the one JSON-able snapshot shape (SSR
**and** the socket render from it — they can't drift). `web/ws/`:
`protocol.py` (pure frames + tolerant `parse_intent`), `board_hub.py`
(server-authoritative, one `asyncio.Lock` ⇒ sequential ops = the 3rd single-
instance layer; FastAPI-free so pollers can broadcast; `get_board_hub()`
singleton). 3 new lifespan pollers: `presence_poller` (diff→PATCH + caches
`state.presence_by_uuid`), `api_disabled` (slow `/v3/player` probe →
`state.api_active_uuids`), `lifecycle_task` (grace-open then one-txn wipe:
WIN→`success_count`, purge placements/RSVPs, `wiped_at`+inactive,
`BOARD_WIPE`). Routers: `staff.py` is now the hub (status + organiser
claim + the Phase-1 password tools kept), `organizer.py` (`/staff/board`
SSR + `WS /staff/board/ws` + a REST twin for **every** mutation, all through
`board_hub.handle`), `roles_dash.py` (`/staff/roles` read view).
Templates `staff/{home,board,_board,roles}.html` + `macros/person.html`;
`static/js/board.js` (thin: WS signal ⇒ re-fetch the `#board` fragment;
SortableJS drag ⇒ MOVE) + **vendored** `sortable.min.js`; CSS board/person/
legend. 103 pytest green (~1.3 s); boots with all 7 pollers; authed board
renders all 7 status-border patterns (the CB non-colour channel).

**Phase 2 durable decisions (not derivable from code):**
- **No schema/Aerich migration** — Phase-1 models already had the full board
  schema; verified by the seeder rebuild + 103 tests on `generate_schemas`.
- **Convergence = full-snapshot PATCH** after every mutation (the simplest-
  correct model `ws_protocol.md` endorses for a low-volume tool); the
  presence poller sends *granular* `presence` ops; HELLO/reconnect ⇒ fresh
  `WELCOME` (no delta replay). **board.js never templates** — it re-fetches
  the SSR `#board` fragment on any WS signal, so there is one render path.
- **WS is tested against the hub directly** (a `FakeClient`), **not over a
  real socket**: the project test transport (httpx `ASGITransport`) has no
  websocket/lifespan by design, and a Starlette `TestClient` would cross
  event loops vs the in-memory Tortoise fixture. Intentional test-scope
  choice — the hub *is* the substance (seq/grace/single-instance/idempotency
  all covered); `organizer.board_ws` is thin glue reusing `deps.read_session`.
- **`PLAYER_ADD` is idempotent**: an already-on-board player is a no-op —
  never moved back to Unassigned, never duplicated (single-instance). The
  WS intent and the REST twin share `board_hub.handle` (one path).
- **Grace freeze is computed live** by `board_hub` via `schedule.phase_of`
  (not a stored flag) so a clock skew can't strand the board; in GRACE only
  `PARTY_SET{result,stage}` is accepted, everything else `REJECTED`.
- **api-disabled inference is best-effort secondary**: online-merge is the
  primary signal (consumed in `presence_poller`); the probe only ever *adds*
  a hidden player as `ONLINE_ELSEWHERE`, unconfirmable ⇒ `UNKNOWN` (never
  fabricate online); a failed probe carries the prior inference (per-uuid
  last-good).
- **conftest `_offline` autouse**: no unit test touches the network — the
  WAPI profile + Mojang last-resort are stubbed to "nothing found"; a test-
  body `monkeypatch` / injected `mojang=` still wins (test_auth_flow /
  test_identity unaffected). Made the suite deterministic + ~1.3 s.
- Jinja **autoescapes apostrophes** — assert an apostrophe-free slice of a
  rejected reason in rendered HTML (the raw WS-frame reason is unescaped).

**Phase 1 done (2026-05-18):** OWN-token `services/wapi.py` (priority-queue
worker, RateLimit/-429 backoff) + `tempserver.py` + AppState + 4 lifespan
pollers (stamp/staff/online_merge/weapons, copied temp-server resilience);
pure `domain/` (identity, membership, capability, attendance, presence,
roles, colourblind); low-trust `web/auth.py`; routers public(login/overview)
+ user(`/me` General+Specific, HTMX self-refresh) + capability CRUD +
**Phase-1-minimal** staff (login + password reset/rotate only — full board is
Phase 2); chips/pills/bars macros (glyph+label+pattern always emitted);
dashboard/modal/fragment templates + CSS. 52 tests green; boots end-to-end
with pollers degrading gracefully offline. **Decisions:** passwords hash with
passlib **pbkdf2_sha256**, not bcrypt (passlib 1.7.x ⨯ bcrypt 4.x self-test is
broken; also dodges the 72-byte cap). `domain/presence.py` is implemented now
(the Specific module needs it) but its live poller + full status sweep are
Phase 2. `domain/buckets.py` is intentionally absent until Phase 2 (board
mutation path). Weapons catalog is best-effort: an empty/odd WAPI result
degrades to "accepted, unverified" rather than blocking capability edits.
