# Data model

Tortoise-ORM models in `app/db/models.py`. Identity anchor = `AnniPlayer.mc_uuid`.

| Model | Purpose / key points |
|-------|----------------------|
| `AnniPlayer` | UUID-keyed person cache. `mc_username` (resolved) vs `wynn_username` (possibly-stale in-game) for rename desync. `last_online` epoch sentinel == API-disabled. `password_hash` null = zero-friction login (first set sticks; staff-resettable). |
| `RoleCapability` | One per (player, role); `confidence`, `build_quality`, `success_count`. `unique_together(player, role)`. |
| `RoleCapabilityWeapon` | Weapons for a capability — **1–3, API-validated** (`MAX_WEAPONS_PER_CAPABILITY`); e.g. primary on Labyrinth + Revolution. |
| `AnniEvent` | One announced anni. Exactly one `is_active` (enforced in `lifecycle.py`). `stamp_epoch` drives the 2 h grace + wipe. `organizer` FK. |
| `Party` | 10-slot party; `ordinal`, `host`, `world`, `stage` 1–5, `result`. `unique_together(event, ordinal)`. |
| `BoardPlacement` | **Single-instance-per-person.** `unique_together(event, player)`; exactly one of (`bucket`, `party`) non-null; `assigned_role` null = gray. Every move = UPSERT in a transaction. |
| `Rsvp` | Per (event, player); `notice` ∈ `RSVP_HARD`/`RSVP_SOFT` (only stored notices); revoke = soft (`revoked_at`). `unique_together(event, player)`. |
| `AppConfig` | key/value runtime config + admin-rotatable staff password hash, timing overrides. **No colourblind key** — CB is a per-user cookie only (no global/event/admin default; world default is always full colour). |
| `MojangNameCache` | uuid→username for offline rename-desync resolution. |

## The single-instance invariant
The spec demands "at most one instance of everyone on the board". Guaranteed at
three layers: (1) DB `unique_together(event, player)` on `BoardPlacement`;
(2) every move is an UPSERT of that one row inside a transaction (never
insert-then-delete); (3) `board_hub` applies ops sequentially on the event loop
(SQLite single writer). A rejected concurrent move sends `REJECTED` so the
client rolls back its optimistic DOM.

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
