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

## Discord bots in this workspace — command prefixes

fishbot's prefix-command leader is **`\`**. The vets ecosystem runs four Discord bots, each with its own prefix; the table is duplicated across each bot's repo so the mapping is discoverable from any vantage point:

| Bot | Repo | Prefix |
|-----|------|--------|
| dazebot | `../dazebot` | `~` |
| nazbot | `../temporary-server` | `!` |
| **fishbot** | `vets-anni` (this repo) | `\` |
| dynobot | (third-party, no repo) | `?` |

Slash commands (e.g. `/rsvp`) are unprefixed. The prefix only applies to text/message commands.

## Hard rules (do not violate)

- **Identity anchor is the Minecraft UUID.** Never key anything on username.
- **vets-anni uses its OWN WAPI token** (separate ratelimit bucket — mandated).
  Never reuse the dazebot/temporary-server token.
- **Online truth = mirror vetsmod `/wv list`** (merge `api.wynnvets.org`
  `/v1/outbound/{list,roster,aliases}` + WAPI guild online + grace cache),
  plus WAPI per-player for board members outside the guild (integration.md).
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
- **API boundary:** vets-anni is one of the three server-side projects —
  alongside `../temporary-server` and `vets-auth` — permitted to call
  dazebot's `/api/internal/*` directly. All three live on the private
  `verify` Docker network. Client-side / public-facing projects (notably
  `../vetsmod`) must route through `../temporary-server` at
  `api.wynnvets.org` and may not call dazebot directly.
- Durable docs live in `.claude/*.md` (indexed above) and stay in version
  control; link new ones here. Use `.claude/ephemeral` for temporary work.
- Tasteful comments throughout; every package has a one-responsibility
  docstring. Modular & migratable — future expansion is expected.

## Status

Build in progress, phased: **0** ✅ skeleton+deploy → **1** ✅ App1 (user
web) → **2** ✅ App3 (staff/board) → **3** App2 (fishbot) → **4** App4
(vetsmod, deferred & coordinated). See the plan file for per-phase scope +
verification. App4 vetsmod surface shipped 2026-06-18 (S7 completion of the
multi-stage cross-repo plan).

**Status palette + gone-as-a-stamp (2026-08-24):**
- **Status borders moved onto the ROLE palette**, each step echoing the role
  it evokes: FILL aqua in-party, HEALER green on-world, SECONDARY sun-gold
  elsewhere, PRIMARY red offline, grey unknown. This inverts the earlier
  arrangement where statuses had the mid-tone family to themselves precisely
  so they could NOT be confused with roles — the judgement changed, not the
  constraint: a deliberate echo teaches faster than an unrelated palette
  avoids confusion, and the two never share a channel anyway (role = card
  background / glyph swatch, status = border). RSVP tickets keep the mid-tone
  family, being objects ON the card. JADE lost its last consumer and went.
- **`OFFLINE_GONE` is retired as a status** and is now an avatar stamp (white
  `?`). It was history, not a degree of presence, and as a sixth border it
  *overwrote* the present — an organiser could not see "not here" and "and
  they were, ten minutes ago" at once. `board_view.is_gone` derives it per
  render from `OFFLINE` + `AppState.seen_online_uuids`, so it tracks live
  presence and needs nothing stored. The `dot` CB pattern retired with it.
- **Gone outranks late**: at most one stamp draws. Both facts stay in the
  view model (they are independent), but "not here" is what gets acted on
  while "was late getting here" is water under the bridge. Logging back on
  restores the hourglass with no state to unwind, because gone is derived.
- CYAN keeps its Okabe-Ito **black** CB hue even though it now backs a
  border: under cb the ring is drawn OUTSIDE the card, against the pale
  dropzone, so black is the highest-contrast option there rather than an
  invisible one.

**Board/roles trims (2026-08-24):**
- **The board card's "remove from board" ✕ is gone**, and so is the whole
  path behind it — `PLAYER_REMOVE`, the hub branch, `buckets.remove_player`,
  the REST twin. Taking a card off tonight's board is what dragging it to
  Sitting out already does; a second mutation path to the same end is exactly
  the kind of thing that grows a divergent caller later. A smoke test asserts
  the *route* 404s, not just that the button is absent, so a half-revert
  can't leave a live mutation endpoint with no UI.
- **/staff/roles can delete ANY profile**, not just empty shells.
  `players.purge` lost its veto and `players.blockers` became
  `players.holdings` — a warning the confirmation dialog spells out
  ("this CANNOT be undone: they have declared 2 role capabilities…") rather
  than a refusal. The shells-only rule read as prudent and wasn't: the ghosts
  staff most need gone are the ones a typo already attached an RSVP to, so
  the button was greyed out on precisely the rows it existed for.
  `deletable_uuids()` → `holdings_by_uuid()`, same bulk-query shape, and a
  test pins it against the per-player form so the dialog can't promise
  something different from what happens.
- **Hourglass**: opaque white on a semi-transparent black disc (no ring),
  stamped dead-centre on the avatar. Translucent `background`, NOT `opacity`
  on the element — that would fade the mark along with the disc, and the mark
  is the point. Centring also settles the vertical-compactness question by
  construction: the badge is smaller than the 32px face, so it cannot reach
  past the card's content and can never set a row's height. (The earlier
  corner placement had to be hand-tuned against the tier pill and sat 1.5px
  low, because its ring was a `box-shadow` — those live outside the border
  box, so `getBoundingClientRect` under-reports where the badge visibly ends.
  Worth remembering for any badge measured that way.)
  Redrawn twice before it worked at size: the final one is OPEN — two fat
  bars and an ✕ — because a filled bowtie's waist is the first thing to fill
  in when the icon is halved, whereas the triangular VOIDS an ✕ leaves hold
  their shape much longer. Negative space downscales better than thin
  positive features.

**Board lanes + lateness rework (2026-08-24):**
- **The LATE sub-bucket is gone.** Lateness stopped being a partition of the
  queue once its threshold started sliding with the RSVP — T-15 hard, T-35
  soft, T-50 walk-in, never for a retraction
  (`constants.LATE_ARRIVAL_SECONDS`, `hot_window.is_late_arrival`). It is now
  a fact about one card and renders as an hourglass over the avatar.
  Evaluated ONCE at insert, inside `buckets.ensure_placed`/`add_walkin` rather
  than by the caller, so the three insert paths can't drift; stored on
  `BoardPlacement.is_late`. `buckets.move` deliberately lost its `is_late`
  parameter — while it was a lane every move supplied one, and a REST move
  defaulting it to False would have silently laundered the hourglass away on
  every drag. `hot_window.is_late_bucket` kept its name but now only drives
  the `live` pill label.
- **Unassigned lanes are main → soft → walk-in**; `is_walkin` is the only
  stored one left. The two derived soft lanes use *different* predicates on
  purpose: Unassigned's `is_soft` takes every soft RSVP (it is a queue you
  place *from*, so you want all the maybes together) while a party's
  `is_offline_soft` takes only the ones who haven't turned up (the slot is
  already allocated). The party lane auto-promotes back into the party proper
  the moment presence goes online — free, because it is derived.
- **Retracting moves the card to Sitting out from anywhere**, a party
  included (`demote_on_revoke`). It used to only demote out of Unassigned on
  "staff intent wins" reasoning; that was backwards for the case that
  matters — a party member pulling out is exactly when the seat needs
  freeing. Re-RSVPing requeues them, so it is not a dead end.
- **The LATE and Retracted text pills are gone** — the hourglass and the torn
  ticket say the same things in less space, and `rsvp_revoked` left the view
  model with them (`rsvp_state` already carries it).

**Board channels rework (2026-08-24):** the person card now carries **two
independent axes** instead of one overloaded border.
- **Status border = presence only.** `OFFLINE_HARD`/`OFFLINE_SOFT` are gone,
  replaced by a single `OFFLINE`. The CB pattern ramp lost its
  `dash-dash-dot` step accordingly.
- **`PaletteColor` split into two tonal families.** The role↔status pairing
  is gone; roles keep the **neon** entries (RED/YELLOW/GREEN/BLUE/CYAN/
  MAGENTA) and the status ramp gets its own **mid-tone** set, picked as a
  group so the six borders read as one scale: TEAL `#37c5c8` (in party) →
  JADE `#3ac56e` (on world) → LIME `#93bd42` (elsewhere) → BRICK `#c93e36`
  (offline) → ORCHID `#c13eab` (gone), GREY unknown. Disjoint families mean a
  role hue and a status hue can never be confused on a card. CB hues *do*
  repeat across families (nine Okabe-Ito hues, sixteen assignments) — safe,
  because a role is always a background/swatch and a status always a border;
  the tested invariant is that the *status* hues stay mutually distinct in
  both palettes. **(Superseded — see "Status palette + gone-as-a-stamp":
  statuses moved onto the role palette and the mid-tone family became the
  RSVP tickets alone.)**
- **`OFFLINE_GONE` finally means what it says.** It used to be inferred from
  "offline with no RSVP"; it now keys off real history —
  `AppState.seen_online_uuids`, accumulated by `presence_poller` and cleared
  by the grace-wipe — surfaced as `PresenceInputs.was_online`. The user
  dashboard reads the same set so its bar agrees with the staff border.
- **RSVP is its own ticket** (`RsvpState` + `RSVP_STYLES` + `rsvp_chip`), left
  of the avatar and the same height as it. All four states are tickets —
  dashed+exclamation (walk-in), tick (hard), clock (soft), torn in half
  (retracted) — hand-pathed on a 24×20 grid in new
  `templates/macros/icons.html` to match the `fa-slab-duo fa-regular
  fa-ticket` reference (~1.25:1, NOT the wider real-world ticket a first pass
  assumed), with **two** perforation bites per end. One central notch reads
  as a waist and the silhouette turns into a spool; two shallower ones read
  as perforations, and that edge is the whole visual argument that the thing
  is a ticket. Notch depth is capped by outline weight — ink grows outward
  from the path and swallows a bite shallower than the line — so the rings
  run lighter than a true slab, and the walk-in is dashed rather than dotted
  (round dots ate the notches and corners outright). The app vendors every asset and hand paths dodge icon-font
  attribution. `REVOKED` outranks the stored notice, and
  `rsvp.states_by_uuid` is the codebase's one *unfiltered* Rsvp read — a
  revoked row is a state to render, not an absence to hide.
- **Ticket construction, three layers of one hue.** The hue's `-dark` shade
  is a FILL on the ticket path (never a plate behind the icon, so it stops at
  the ticket's edge) and the `-light` tint is the linework. The silhouette is
  stroked three times over the same path — white, colour, black, widest
  first — with the body fill drawn LAST, so it clips the inner half of every
  ring and the edge reads outward as black hairline → coloured slab → white
  hairline. SVG has no inner border and cannot offset a stroke to one side of
  its path; the ring stack is the only way to get an asymmetric edge. The
  fill has to reach the path from a CSS rule (`--tk-fill`) because `var()` is
  inert inside an SVG presentation attribute, and the passes are emitted
  inline rather than via `<use href="#id">` because ids repeat across ~40
  cards on one board. Two variants run thinner linework for opposite reasons:
  dashed (fatter dashes fuse into a solid rule) and torn (two outlines facing
  each other eat the whole tear channel).
- **The tickets share the mid-tone family** with the status ramp — TEAL hard,
  LIME walk-in, ORCHID soft, BRICK retracted. They had their own
  near-duplicate entries (AQUA/AMBER/BROWN) for a while and those earned
  nothing: a hue one nudge off TEAL is not a distinction anyone can use, just
  two hexes to keep in step. A border and a ticket landing on the same hue is
  fine — a thin ring versus a plated icon, never confusable in form.
  A static test asserts the macro draws a branch for every state, since a
  missing one renders an invisible empty `<svg>` rather than failing loudly.
- **"Offline soft RSVPs" sub-bucket** in Unassigned (above walk-ins) and in
  every party (rendered only when occupied). **Derived, not stored**
  (`board_view.is_offline_soft`): soft RSVP + `OFFLINE`, never `UNKNOWN`. It has to be derived — it tracks live presence and party
  placements have no lane flags — so its dropzone deliberately targets the
  same container as the lane above it and a drag in/out of it re-derives.
  Carved out of the **main** Unassigned lane only (walk-ins never RSVP'd;
  LATE is a provenance marker worth keeping whole). A party's `count` spans
  both lanes so the N/10 header can't lie.
- **Win credit is party-only.** `_credit_wins` now spells out
  `party_id__isnull=False, bucket__isnull=True`: Unassigned / Volunteering /
  Sitting-out earn nothing even holding an assigned role (a role is routinely
  set before, or left set after, a card is dragged into a party). The
  `party__result__in` join happened to drop bucketed rows already — via
  `NULL IN (...)` on a LEFT JOIN — and that is far too subtle a thing for
  this rule to rest on.

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

## External name-resolution providers

(Preemptive — no current code consumes this, but the convention is recorded so it's already in place when name → UUID or UUID → name lookups are added.)

Reliability ladder: `ashcon < wynncraft < playerdb < mojang`.

| Provider | Accuracy | Rate limit |
|---|---|---|
| ashcon | low (frequently stale) | very permissive |
| PlayerDB | medium | medium-permissive (not unlimited) |
| Wynncraft `/v3/player` | only authoritative for Wynncraft-internal state | shared with this service's other Wynncraft traffic |
| Mojang | source of truth | very restrictive |

**This repo is server-side.** We own the Mojang and PlayerDB quotas exclusively on this box, so load is predictable. Prefer accuracy when we have headroom — PlayerDB as the primary upstream, Mojang reserved for authoritative tiebreaks and writing fresh names to the cache. Skip ashcon (PlayerDB does the same job better). Stay well below each tier's budget so it's always available when truly needed.

When using a permissive provider, treat its `username` field as potentially stale (PlayerDB and ashcon are known to retain old names). Before writing a name to any long-lived cache, confirm against Mojang; if that fails, skip the cache write rather than persisting a known-stale value.

Reference implementations: dazebot's `lib/mc/mojang.py` (name → UUID via `get_mc_uuid`, UUID → name via `get_mc_username`) and temporary-server's `app/services/username_cache.py` / `app/services/guild_roster_poller.py` (Wynncraft-key-vs-cache tiebreaker pattern).
