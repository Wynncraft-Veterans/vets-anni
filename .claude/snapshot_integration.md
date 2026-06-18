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

## Endpoints (S1 + S5 + S6 + S7)

All live under `/api/internal/` and require `X-Introspect-Secret`
matching `settings.anni_introspect_secret`. Fail-closed when the setting is
unset (503).

| Method | Path | Body | Returns | Added |
|---|---|---|---|---|
| GET | `/anni-eligibility` | — | `{uuids: ["...", ...]}` | S1 |
| GET | `/anni-player/{uuid}` | — | one snapshot, or 404 | S1 |
| POST | `/anni-snapshot-batch` | `{uuids: [...]}` | `{snapshots: [...]}` | S1 |
| POST | `/anni-party-scrollspot` | `{actor_mc_uuid, scroll_spot: {x,y,z}\|null}` | `{status: "ok"}` | S5 |
| POST | `/anni-rsvp-by-uuid` | `{actor_mc_uuid, notice: "hard"\|"soft"\|"revoke"}` | `{status: "ok"}` | S6 |
| POST | `/anni-party-observation` | `{observer_mc_uuid, party_member_usernames, leader_username, world}` | `{status: "ok", resolved, dropped}` | S7 |

`/anni-party-scrollspot` is the per-party host's write path for the in-game
scroll-spot coordinate. Temporary-server's `anni_scrollspot_set` inbound
handler forwards the authenticated session's MC UUID as `actor_mc_uuid` —
no impersonation possible. vets-anni then looks up the actor's currently
assigned party in the active event and accepts the write iff the actor is
that party's `host`. 403 otherwise. Cleared automatically at grace-wipe.

`/anni-party-observation` is the S7 vetsmod back-report path. When a
connected vetsmod client sees an organiser username in its local Wynncraft
party, it forwards `{party_member_usernames, leader_username, world}` via
temp-server's `anni_party_observation` inbound handler — which stamps the
authenticated session's MC UUID as `observer_mc_uuid` and forwards here.
The endpoint resolves the names via [`AppState.resolve_uuid`](../app/services/state.py)
(cached roster → legacy-name alias fallback), drops unresolvable names,
and writes `{member_uuid: leader_uuid}` pairs into
`state.party_leader_by_uuid`. The observer's session UUID is always written
even if their username didn't resolve (a brand-new member whose roster row
hasn't ingested yet). An unresolvable leader short-circuits the whole
observation (no anchor for the `ONLINE_PARTY` upgrade — the dict isn't
mutated). `state.party_status_fetched_at` is touched on every successful
write; entries older than `_PARTY_LEADER_TTL_SECONDS` (60 s) are treated as
stale by [`presence_poller`](../app/services/presence_poller.py) so a
vetsmod disconnect mid-window doesn't pin a user to yellow forever.

`/anni-rsvp-by-uuid` is the in-game `/wv anni rsvp <hard|soft|revoke>`
write path. Temp-server's `anni_rsvp` inbound handler forwards the
authenticated session's MC UUID as `actor_mc_uuid`. vets-anni delegates
to [`app/domain/rsvp_by_uuid.execute_uuid_rsvp`](../app/domain/rsvp_by_uuid.py),
which reuses the cog's `set_rsvp` / `revoke` / `_auto_place_after_rsvp`
/ `_broadcast_board_snapshot` / `_post_public` chain — same Rsvp row,
same RSVP_CHANNEL_ID confirmation post as a Discord `\rsvp`. Brand-new
UUIDs get a placeholder `AnniPlayer` row (`mc_username = uuid[:8]`,
`is_placeholder=True`) which the next auto-promoter / presence cycle
hydrates. 404 on no active event, 409 on T-90 cutoff (revokes pass through
the cutoff). No schema-version bump — `board.rsvp.notice` is already on
the snapshot at v2/v3.

`board.wont_reason` (S6 refinement) — when `state == "wont_assign"`,
this field distinguishes the two paths that land a player there:
- `"RSVP retracted"` — the player has a revoked `Rsvp` row for the
  active event (`Rsvp.revoked_at IS NOT NULL`). Demote was almost
  certainly the auto-revoke path
  ([`buckets.demote_on_revoke`](../app/domain/buckets.py)).
- `"Sitting out"` (`BUCKET_LABEL[WONTASSIGN]`) — no revoked Rsvp;
  staff manually moved the player to WONTASSIGN.

Re-RSVP after revoke calls
[`buckets.promote_from_wontassign`](../app/domain/buckets.py) FIRST,
so a hard/soft RSVP from any WONTASSIGN state moves the player back to
main UNASSIGNED (and `wont_reason` is no longer surfaced because the
state flips to `unassigned`). The cog's "RSVP'd users always go to main
lane, never LATE" rule still applies.

The eligibility list is "every `AnniPlayer` row" per Hard Rule #3 — once
vets-anni knows a player at all, they're plausible. Tier-specific filtering
(e.g. excluding OTHER even when registered) is intentionally not implemented;
flip the dial in `app/domain/snapshot.py::push_eligible_uuids` if it ever
needs to.

The batch endpoint reuses one `get_active_event()` lookup across all UUIDs
in the batch — so an N-uuid tick costs 1 event read + N player reads, not
2N. Missing/erroring UUIDs are silently absent from the response (better
partial batch than a failed tick).

## Schema (`schema_version: 3`)

```jsonc
{
  "schema_version": 3,
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
    } | null,
    "all_parties": [             // schema v2; empty list when no parties yet
      {
        "ordinal": 1,
        "members": [
          {"uuid": "...", "username": "...", "role": "TANK"}
        ]
      }
    ]
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
      "members": [{"uuid": "...", "username": "...", "role": "FILL"}],
      "scroll_spot": {"x": 345, "y": 45, "z": -1315} | null   // S5
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
  "organisers": ["uuid1", "uuid2"],          // lead + every party host
  "organiser_usernames": ["name1", "name2"]  // parallel order; S7 gate
}
```

## Field semantics — gotchas worth pinning

- `event.stamp_epoch` is **null when past/unknown**, *not* the past value.
  Past stamps surface only as the anchor for `event.prediction`. vetsmod's
  S2 rule "don't print a prediction line unsolicited" hinges on this:
  the anni-motd renderer reads `event.announced && event.stamp_epoch`;
  the manual `/wv anni` reads `event.prediction` when stamp is null.
- `event.prediction` is computed against the *most recent past* anni
  stamp in the DB, not a hardcoded anchor. Whenever any past `AnniEvent`
  exists the prediction is populated — even when
  `get_active_event()` returns `None` and so there's no "active" event
  row to anchor on, the assembler still synthesises an `event` block
  with the prediction. Only when the DB has *zero* `AnniEvent` rows of
  any kind does the whole `event` field collapse to `null` (legacy
  "not announced" string applies).
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
- `organisers` is `[lead, host_party1, host_party2, ...]`, de-duplicated by
  UUID, stable order. `organiser_usernames` is the parallel projection (same
  length, same order). S7's vetsmod-side gate runs on the usernames — Wynncraft
  exposes party members by username only, so name-based matching is the only
  reliable path. Both lists are computed from one query pair so concurrent
  host reassignments cannot desync UUID order from username order.
- `event.all_parties` (schema v2) is the lightweight per-party member listing
  vetsmod's S4 outline registry tiers against. Each member entry is
  `{uuid, username, role}` so vetsmod (which keys by username — see
  `outlines.md` §2.4) can match `level.players()` names without a UUID
  round-trip. `all_parties` is always present on a non-null `event` block —
  empty list when no parties exist yet (early board-assembly, prediction-only
  fallback). Members come from the same `_party_member_refs` projection as
  `board.party.members`, so the two views stay drift-free. `board.party` for
  the local player is a richer per-row block (world, host, etc.);
  `all_parties` is a flat enumeration without those extras.
- `board.party.scroll_spot` (schema v3) is **only on the local player's own
  party**, not in `all_parties`. Cross-party scroll-spot visibility is out
  of scope. `null` until the party host writes one via vetsmod's
  `/wv anni scrollspot {set|here|clear}`; cleared automatically at
  grace-wipe. The three columns (`scroll_spot_x|y|z`) are nullable together;
  the snapshot collapses them to `null` if any one is unset.

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
