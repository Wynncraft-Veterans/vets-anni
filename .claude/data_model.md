# Data model

Tortoise-ORM models in `app/db/models.py`. Identity anchor = `AnniPlayer.mc_uuid`.

| Model | Purpose / key points |
|-------|----------------------|
| `AnniPlayer` | UUID-keyed person cache. `mc_username` (resolved) vs `wynn_username` (possibly-stale in-game) for rename desync. `last_online` epoch sentinel == API-disabled. `password_hash` null = zero-friction login (first set sticks; staff-resettable). `preferred_regions` = user-set CSV of MaxMind GeoIP2 continent codes (`ContinentCode`; "" = any) — parsed via `app/domain/regions.py`; shown on the General module + the staff person card. |
| `RoleCapability` | One per (player, role); `confidence`, `success_count`. `unique_together(player, role)`. Reliability is derived, not stored (`domain/reliability.py`; it replaced the self-declared `build_quality`, dropped in migration 6). A staff override re-bases it: `reliability_override` (tier, null = none), `reliability_set_at`, and `reliability_set_wins` (the `success_count` snapshot, since wins have no dates) — migration 7. |
| `Setback` | One bad outcome written by the grace-wipe: `kind` LOSS (with `role`) or MISSED (hard RSVP no-show, `role` null = every role), `occurred_at` = the anni's stamp. Dated, not counted, because reliability forgives one a month. |
| `RoleCapabilityWeapon` | Weapons for a capability — **1–3, API-validated** (`MAX_WEAPONS_PER_CAPABILITY`); e.g. primary on Labyrinth + Revolution. |
| `AnniEvent` | One announced anni. Exactly one `is_active` (enforced in `lifecycle.py`). `stamp_epoch` drives the 2 h grace + wipe. `organizer` FK. |
| `Party` | 10-slot party; `ordinal`, `host`, `world`, `stage` 1–5, `result`. Optional `scroll_spot_{x,y,z}` (S5; host-set via vetsmod, cleared at grace-wipe). `unique_together(event, ordinal)`. |
| `BoardPlacement` | **Single-instance-per-person.** `unique_together(event, player)`; exactly one of (`bucket`, `party`) non-null; `assigned_role` null = gray. Every move = UPSERT in a transaction. |
| `Rsvp` | Per (event, player); `notice` ∈ `RSVP_HARD`/`RSVP_SOFT` (only stored notices); revoke = soft (`revoked_at`). `seen_online_at` = first hot-window sighting (presence poller), persisted so a restart can't manufacture a missed RSVP. `unique_together(event, player)`. |
| `AppConfig` | key/value runtime config + admin-rotatable staff password hash, timing overrides. **No colourblind key** — CB is a per-user cookie only (no global/event/admin default; world default is always full colour). |
| `MojangNameCache` | uuid→username for offline rename-desync resolution. |

## The single-instance invariant
The spec demands "at most one instance of everyone on the board". Guaranteed at
three layers: (1) DB `unique_together(event, player)` on `BoardPlacement`;
(2) every move is an UPSERT of that one row inside a transaction (never
insert-then-delete); (3) `board_hub` applies ops sequentially on the event loop
(SQLite single writer). A rejected concurrent move sends `REJECTED` so the
client rolls back its optimistic DOM.

## Deleting an `AnniPlayer` (`domain/players.py`)
Rows are created lazily and generously — `buckets.add_walkin` ("Add Players"),
`set_organizer`, `\rsvp set` and the auto-promoter all get-or-create by
whatever uuid resolved — so a typo that happens to be a real Minecraft name
becomes a ghost profile. `players.purge` is the **only** delete path, and it
destroys **any** profile — `BoardPlacement`, `RoleCapability` and `Rsvp` rows
cascade with it; `Party.host` and `AnniEvent.organizer` are `SET_NULL`, so
those survive minus a name.

It used to refuse anything holding real user data. That sounded prudent and
wasn't: the ghosts staff most need gone are exactly the ones a typo has
already attached an RSVP to, so the button sat there greyed out on the only
rows anyone wanted it for. What replaced the veto is a *warning* —
`players.holdings()` (and its bulk twin `holdings_by_uuid()`, which the roles
dashboard renders every row's confirmation from) names what a delete would
destroy, and the dialog says it before you commit.

There is no "remove from the board without touching the profile" any more:
that is what dragging someone to Sitting out already does, and a second
mutation path to the same end was redundant. Note that neither leaving is
permanent for an online guild member — the auto-promoter re-materialises a
placeholder + placement on the next hot-window tick.

## Migrations (Aerich)
Deliberate divergence from dazebot (which has none). Workflow:

```
aerich init -t app.db.config.TORTOISE_ORM     # once
aerich init-db                                # once: creates migrations/ + initial
# per schema change:
#   edit models.py
aerich migrate --name <desc>                  # commit the generated file
aerich upgrade                                # apply (Docker entrypoint runs this)
```

`migrations/` is **committed** (not gitignored) so prod applies the exact
reviewed set via `manage update vets-anni`. Tests use in-memory SQLite +
`generate_schemas` (no Aerich) via `lifecycle.init_for_tests`.

**Phase 2 added no migration:** the full board schema (`Party`,
`BoardPlacement`, the `unique_together(event,player)` invariant, `Rsvp`,
`grace_opened_at`/`wiped_at`) already shipped in the Phase-1 models, so the
staff/board work is pure behaviour over the existing schema —
`app.domain.buckets` is the only writer of `BoardPlacement` (UPSERT in a
transaction). Verified by the seeder rebuild + the full suite on
`generate_schemas`.

**Aerich-on-SQLite gotcha:** `aerich migrate`'s auto-diff aborts with
`NotSupportError: Alter column comment is unsupported in SQLite` whenever the
diff touches a field whose *description* drifted from the last migration's
`MODELS_STATE` snapshot (SQLite can't `ALTER … COMMENT`). When that happens,
generate the migration deterministically instead: write the `ALTER TABLE`
upgrade/downgrade by hand into a `N_<ts>_<name>.py` file using
`aerich.migrate.MIGRATE_TEMPLATE`, with `MODELS_STATE =
aerich.utils.get_formatted_compressed_data(get_models_describe("models"))`
computed against the *current* models (init Tortoise with an in-memory
connection so `data/` is never touched). Refreshing `MODELS_STATE` to match
reality also realigns the baseline so the *next* `aerich migrate` diffs
cleanly. This is phase-1 greenfield: there is no production data, so a stale
local `data/anni.db` can simply be deleted and re-seeded rather than migrated.
