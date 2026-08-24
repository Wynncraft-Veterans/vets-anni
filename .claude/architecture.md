# Architecture

## Process model
One Python process, one asyncio loop (lean for the 2 GB VPS shared with ~9
stacks). It hosts:

- **FastAPI** (uvicorn) — App1 user dashboard + App3 staff/organizer board.
- **fishbot** (discord.py) — App2 `/rsvp`, started as a lifespan task.
- **pollers** — anni stamp, online-merge, staff, weapons, presence,
  api-disabled, lifecycle/grace-wipe — lifespan asyncio tasks (pattern copied
  from temporary-server `app/__init__.py:_lifespan`).

Precedent: dazebot runs FastAPI in-process with its bot; temporary-server runs
pollers as lifespan tasks. We follow both.

## Stack
FastAPI + Jinja2 SSR + HTMX + Alpine.js + SortableJS (vendored, **no build
step**) + one WebSocket for the live board. Tortoise-ORM + **Aerich** + SQLite
(`./data` volume, no DB sidecar). CSS mimics Alvin's `returns/56/style.css`
(glassmorphism). `uv` is intentionally not required — plain venv+pip.

## Package map (`app/`)
- `settings.py` — env config (cadences runtime-overridable via `AppConfig`).
- `constants.py` — enums + data tables (roles, colours, attendance table, role
  guidance). Pure data; safe to import anywhere.
- `db/` — `models.py`, `config.py` (TORTOISE_ORM), `lifecycle.py`
  (connect + single-active-event invariant), `bootstrap.py` (schema safety net).
- `domain/` — pure logic, no FastAPI/discord: `roles`, `membership`,
  `capability`, `attendance`, `presence`, `buckets`, `players`, `identity`,
  `colourblind`.
- `services/` — pollers + outbound clients: `wapi` (OWN token), `tempserver`,
  `dazebot_client`, `stamp_poller`, `staff_poller`, `online_merge`,
  `presence_poller`, `weapons_poller`, `api_disabled`, `lifecycle_task`.
- `web/` — `deps` (Jinja/sessions/CB), `auth`, `routers/`, `ws/`.
- `bot/` — `client` (fishbot + cog autoload), `cogs/`, `services/`.

## Data flow
`api.wynnvets.org` (`/v1/outbound/{stamp,staff,list,roster,aliases}`) + WAPI
(own token: guild-online, item search, slow player probe) → `services/*`
pollers → an `AppState` cache on `app.state` + DB snapshots → `presence_poller`
→ `board_hub` diffs → board WebSocket clients. HTMX pages read AppState+DB per
request. fishbot writes `Rsvp` rows; `dazebot_client` resolves Discord→MC at
`/rsvp` time. Every client serves last-good cache on upstream failure; a bad
poll tick never kills its loop.

## Phasing
0 ✅ skeleton+deploy · 1 ✅ App1 · 2 App3/board · 3 fishbot · 4 vetsmod (deferred).
Full per-phase scope + verification: the plan file (see CLAUDE.md).
