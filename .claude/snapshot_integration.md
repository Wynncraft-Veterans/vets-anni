# Anni snapshot integration — MWE wire shape

This is the contract for the canonical anni snapshot that flows out of
vets-anni's verify-network internal endpoints, through temporary-server, and
into vetsmod clients. **One shape, two transports** (push + on-demand pull).

## Why one shape

Per the MWE plan's Hard Rule #2: the snapshot push stream and the on-demand
pull return identical objects. vetsmod treats it as opaque transit — every
breaking shape change is a `schema_version` bump, coordinated across all
three repos.

## Where it lives

- **Assembler:** `app/domain/snapshot.py` — pure, takes
  `(AnniPlayer, AnniEvent | None, AppState)` and returns a dict.
- **Endpoints:** `app/web/routers/anni_internal.py` — three secret-gated
  routes that all reduce to `assemble_snapshot`.
- **Prediction helper:** `app/domain/anni_prediction.py` — the
  `\guess`-style window (Uniform 71.4 h..82.0 h on the most recent
  confirmed stamp).

## Endpoints (S1)

All three live under `/api/internal/` and require `X-Introspect-Secret`
matching `settings.anni_introspect_secret`. Fail-closed when the setting is
unset (503).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/anni-eligibility` | — | `{uuids: ["...", ...]}` |
| GET | `/anni-player/{uuid}` | — | one snapshot, or 404 |
| POST | `/anni-snapshot-batch` | `{uuids: [...]}` | `{snapshots: [...]}` |

The eligibility list is "every `AnniPlayer` row" per Hard Rule #3 — once
vets-anni knows a player at all, they're plausible. Tier-specific filtering
(e.g. excluding OTHER even when registered) is intentionally not implemented;
flip the dial in `app/domain/snapshot.py::push_eligible_uuids` if it ever
needs to.

The batch endpoint reuses one `get_active_event()` lookup across all UUIDs
in the batch — so an N-uuid tick costs 1 event read + N player reads, not
2N. Missing/erroring UUIDs are silently absent from the response (better
partial batch than a failed tick).

## Schema (`schema_version: 1`)

```jsonc
{
  "schema_version": 1,
  "mc_uuid": "...",          // primary key — matches AnniPlayer.mc_uuid
  "mc_username": "...",
  "event": {
    "stamp_epoch": 1234567890,   // null when stamp is past/unknown
    "announced": true,           // true iff stamp is future
    "prediction": {              // present iff stamp is past or null
      "earliest_epoch": 1234567890,
      "median_epoch":   1234567890,
      "latest_epoch":   1234567890,
      "window_hours":   10.6
    } | null
  } | null,                  // null iff no AnniEvent has ever been recorded
  "registration": {
    "registered": true,         // has at least one RoleCapability row
    "core": true,               // == registered for now (split if/when
                                // 'fill-only registration' is added)
    "roles": [{"role": "TANK", "title": "Tank (Party Tank)",
               "url": "https://www.wynnvets.org/docs/guild/anni/#tank"}]
  },
  "rsvp": {
    "notice": "hard|soft",
    "updated_at": 1234567890,   // unix epoch
    "revoked": false            // always false in the active row;
                                // present for forward-compat
  } | null,
  "board": {
    "state": "unplaced|wont_assign|unassigned|party",
    "party": {                  // present iff state == "party"
      "ordinal": 2,
      "world":   "EU5" | null,
      "result":  null | "win" | "loss" | "lag",
      "host":    {"uuid": "...", "username": "..."} | null,
      "members": [{"uuid": "...", "username": "...", "role": "FILL"}]
    } | null,
    "role":       "TANK" | null,    // == party.members[i].role for `i==self`
                                    // when state==party; null otherwise
    "wont_reason":"..." | null      // bucket label when state==wont_assign
  },
  "attendance": {
    "band":             1..6,        // worst -> best
    "label":            "Most Likely",
    "notice_effective": "attend_early|rsvp_hard|rsvp_soft|attend_late|null"
  },
  "organisers": ["uuid1", "uuid2"]   // lead + every party host;
                                     // S7 gates the party back-report on this
}
```

## Field semantics — gotchas worth pinning

- `event.stamp_epoch` is **null when past/unknown**, *not* the past value.
  Past stamps surface only as the anchor for `event.prediction`. vetsmod's
  S2 rule "don't print a prediction line unsolicited" hinges on this:
  the anni-motd renderer reads `event.announced && event.stamp_epoch`;
  the manual `/wv anni` reads `event.prediction` when stamp is null.
- `event.prediction` is computed against the *most recent past* anni
  stamp in the DB, not a hardcoded anchor. On a totally empty DB it's
  `null` (legacy "not announced" string applies).
- `registration.core` mirrors `registration.registered` today — a single
  bool would do, but the spec calls for the split so the snapshot keeps
  both fields for forward-compat.
- `board.state` collapses UNASSIGNED+VOLUNTEERS into `unassigned`. vetsmod
  doesn't differentiate; the staff board does, and reads the model
  directly.
- `attendance.band` mirrors the dashboard's General-module bottom bar.
  In-party (`board.state == "party"`) counts as Core for the attendance
  table; everything else is Fill. The raw percentage is **never** in the
  snapshot — only the band index + label (spec rule: exact probabilities
  invite rules-lawyering).
- `organisers` is `[lead, host_party1, host_party2, ...]`, de-duplicated,
  stable order. Used by S7 to gate vetsmod's party-back-report frame.

## Eligibility vocabulary

The poller fetches snapshots for every UUID returned by
`/anni-eligibility`. Per Hard Rule #3 that's every `AnniPlayer` row — the
in-DB presence IS the "vets-anni knows about them" signal. Tier-based
exclusion lives in `app/domain/snapshot.py::PUSH_ELIGIBLE_TIERS` (currently
informational only; the eligibility endpoint is the active filter).

## Coordinating breaking changes

A snapshot field rename or removal:

1. Bump `SCHEMA_VERSION` in `app/domain/snapshot.py`.
2. Add the new field to this doc.
3. Land a vetsmod client-side change that consumes the new shape (gated
   on `schema_version`). Older vetsmod clients stay on the previous
   schema until they update.
4. Once vetsmod ships, remove the old field.

Additive fields (new optional keys) don't need the dance — vetsmod is
expected to ignore unknown keys (Gson's default).

## Where the existing helpers come from

- `app/db/lifecycle.get_active_event` — single active row, organizer
  eager-loaded.
- `app/domain/rsvp.get_current` — active RSVP filter (revoked_at IS NULL).
- `app/domain/attendance.evaluate` + `meta` — the publishable band logic.
- `app/constants.ROLE_GUIDANCE` — role title + docs URL.

The snapshot assembler composes these — it never invents new domain logic.
If you find yourself adding a new computation to `snapshot.py`, that
computation almost certainly belongs in `app/domain/<topic>.py`.
