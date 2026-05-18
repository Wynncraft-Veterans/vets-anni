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
web) → **2** App3 (staff/board) → **3** App2 (fishbot) → **4** App4
(vetsmod, deferred & coordinated). See the plan file for per-phase scope +
verification.

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
