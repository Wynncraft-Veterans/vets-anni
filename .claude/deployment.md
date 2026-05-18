# Deployment & local dev

## vets-deploy stack — `stacks/vets-anni/` (locally-built; mirrors `stacks/dazebot`)
- `docker-compose.yml`: `build` context `./src` dockerfile `docker/Dockerfile`,
  `image: vets-anni:local`, `restart: unless-stopped`, `env_file .env`,
  `volumes ["./data:/app/data"]` (SQLite persists), Traefik labels routing
  `Host(\`anni.wynnvets.org\`)` → container `:8000` (websecure + letsencrypt),
  `com.centurylinklabs.watchtower.enable=false`, `networks: [proxy, verify]`
  (both `external: true`). **No DB sidecar** — in-process SQLite.
- `.env` (gitignored on the VPS) from `.env.example`. `verify` net lets it
  reach dazebot for the anni-identity endpoint.
- `manage.sh` needs no change: its build-stack branch already does
  `git pull ./src` + `docker compose build --pull` + `up -d`.

First-time on the VPS:
```
# DNS A record anni.wynnvets.org -> VPS, then:
manage sync
manage install vets-anni
cd /opt/docker/vets-anni && gh repo clone <org>/vets-anni src
cp .env.example .env && micro .env       # fill secrets; OWN WAPI_TOKEN
manage up vets-anni
curl -s https://anni.wynnvets.org/health   # {"status":"ok"}
```
Update: `cd /opt/docker/vets-anni/src && git pull && manage update vets-anni`.

## Local dev (NB: `uv` is NOT installed — use venv+pip)
```
python -m venv .venv
.venv\Scripts\pip install -e .[dev]          # Windows
# DB schema (once / after model changes):
.venv\Scripts\aerich upgrade                  # or: aerich init-db (first time)
.venv\Scripts\python main.py                  # http://127.0.0.1:8000
.venv\Scripts\pytest                          # in-memory DB, no Aerich
```
`.env` (copy from `.env.example`, `DEBUG` defaults true for dev) is loaded via
python-dotenv. Without a `FISHBOT_TOKEN` the bot is skipped and the web app
still runs fully.

## VS Code Run & Debug (Ctrl+Shift+D)

`.vscode/launch.json` ships two configs (and a compound):

- **vets-anni: dev server** — runs `main.py` with the `.venv` interpreter,
  `DEBUG=true`, auto-reload; open <http://127.0.0.1:8000> to eyeball design /
  rendering.
- **vets-anni: seed dev data** — runs `scripts/seed_dev.py`, which wipes the
  anni-domain tables in the local `./data/anni.db` and inserts a realistic
  dummy dataset: one active `AnniEvent` (~93 min out, organiser set), players
  across every membership tier (incl. an API-disabled epoch player and a
  rename-desync one), multi-weapon capabilities (e.g. primary on
  Labyrinth+Revolution), parties at assorted stages, and bucket/party
  placements + an RSVP. Idempotent — re-run any time.

Typical loop: run *seed dev data* once, then *dev server*, then refresh the
browser. `.vscode/settings.json` points the interpreter at `.venv` so the
editor resolves imports.

## Cross-repo (dazebot)
Add `POST /api/internal/anni-identity` to `dazebot/api/main.py` (reuse the
`X-Introspect-Secret` pattern + `verify_keys.resolve_tier`/`_find_member`). No
dazebot schema/env/migration change. Commit straight to dazebot `main` (its
convention). `DAZEBOT_INTROSPECT_SECRET` must match between the two `.env`s;
confirm the dazebot port from `vets-deploy/stacks/dazebot/.env` (picolimbo uses
9421) for `DAZEBOT_ANNI_IDENTITY_URL`.

## Conventions
Commit straight to `main` for vets-deploy. Git identity for the vets stack is
`wenweia`. Do not commit `.env`, `data/`, or `.venv/`.
