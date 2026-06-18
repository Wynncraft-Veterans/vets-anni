# Integration contracts

## temporary-server — `https://api.wynnvets.org` (read-only, no auth)
- `GET /v1/outbound/stamp` → plain-text unix epoch. Empty/past = no anni
  announced. Single source for every countdown.
- `GET /v1/outbound/staff` → `[{uuid,username,rank,online,server}]` — online
  staff only (temp-server already paid the WAPI cost; no token spent here).
- `GET /v1/outbound/list` → `{connected:[{uuid,username,tier,queued}]}` —
  vetsmod-connected clients; `tier` ∈ guild|waitlist|honourary.
- `GET /v1/outbound/roster` → `{uuid: username}` (authoritative names).
- `GET /v1/outbound/aliases` → `{legacyname_lower: uuid}` (rename desync).

`online_merge` = union of `list` ∪ WAPI `/v3/guild/Returners` online ∪ roster,
with a ~30 s grace cache — mirrors vetsmod `OnlineMemberService`.

## Identity (IGN → UUID) — spare the shared Mojang bucket

`api.mojang.com` is aggressively ratelimited **and that bucket is shared by
every stack on the vets-deploy host**. So `app/services/mojang.py` resolves an
IGN with the cheapest source first and only ever calls the network for a
brand-new, non-guild user's first login:

1. AppState **roster** (whole Returners guild, in-memory) — guild members
   never hit the network at all;
2. AppState **aliases** (legacy names) — offline renames;
3. **MojangNameCache** (our DB, 7-day, write-through);
4. a known **AnniPlayer** row (anyone who logged in before);
5. only then the network: **PlayerDB** (Nodecraft, no ratelimit) → **ashcon**
   → Mojang's *services* host (`api.minecraftservices.com`, a *different*
   bucket). **`api.mojang.com` is never called.**

temp-server has no name→uuid endpoint, but its roster/aliases (already polled
by `online_merge`) cover every guild/renamed case for free. dazebot's
`lib/mc/mojang.py` proves the provider set; we reorder gentle-first because we
want the (stable) UUID, not the canonical current name.

## Wynncraft API — OWN token, separate ratelimit bucket (mandated)
`services/wapi.py` is the only place that sends `WAPI_TOKEN`. Honour
`RateLimit-*` headers, back off on 429 (port of dazebot's Requestor). We spend
the token only on: `/v3/guild/Returners` online, `/v3/item/search/{q}` (weapons
catalog, ITEMS bucket, 1 h cache), and the slow api-disabled `/v3/player/{uuid}`
probe. Heavy reads come from api.wynnvets.org instead.

The single `/v3/guild/Returners` response `online_merge` already fetches is
*also* parsed for the **full staff roster** (every member whose rank is in
`settings.staff_guild_rank_set` — `STAFF_GUILD_RANKS` env, default the
management ranks; online **or offline**) into `state.guild_staff`. That is the
**lead-organiser candidate list** for the board dropdown + the staff hub
(replacing the old board-members-only / online-only sources) — no extra WAPI
call. A picked organiser with no `AnniPlayer` row yet is get-or-created from
the cached guild-staff name in `buckets.set_organizer`.

## dazebot — one added internal endpoint (verify network only)
`POST /api/internal/anni-identity`, header `X-Introspect-Secret` ==
`DAZEBOT_INTROSPECT_SECRET` (reuse the existing fail-closed pattern; no new
secret/model/migration). Body `{discord_id}` → `{linked, disc_uuid, mc_uuid,
mc_username, tier, blocked, reason}`. Implemented by reusing
`lib/staff/verify_keys.py` `_find_member` + `resolve_tier`. fishbot calls it
via `services/dazebot_client.py`; dazebot down ⇒ `/rsvp` degrades gracefully.

## vetsmod (App4 — MWE/anni surface)

**vetsmod never speaks to vets-anni directly.** All vetsmod ↔ vets-anni
traffic transits temporary-server (`api.wynnvets.org`) — there is no
`GET /v1/anni/*` on vets-anni and there never will be. The wire is one
canonical snapshot shape (`app/domain/snapshot.py`, `schema_version` int),
exposed only on the verify-network internal endpoints in
`app/web/routers/anni_internal.py`:

- `GET  /api/internal/anni-eligibility` → `{uuids: [...]}`
- `GET  /api/internal/anni-player/{uuid}` → one snapshot dict
- `POST /api/internal/anni-snapshot-batch` `{uuids:[...]}` → `{snapshots:[...]}`
- `POST /api/internal/anni-party-scrollspot` (S5) — host of a party writes
  the in-game scroll spot; body `{actor_mc_uuid, scroll_spot: {x,y,z}|null}`.
  Host authorisation is verified server-side against `Party.host`.
- `POST /api/internal/anni-rsvp-by-uuid` (S6) — in-game
  `/wv anni rsvp <hard|soft|revoke>` write path. Body
  `{actor_mc_uuid, notice}`. Delegates to
  [`app/domain/rsvp_by_uuid.execute_uuid_rsvp`](../app/domain/rsvp_by_uuid.py)
  which reuses the cog's `set_rsvp` / `revoke` / `_auto_place_after_rsvp` /
  `_broadcast_board_snapshot` / `_post_public` chain, so an in-game RSVP
  is byte-equivalent to a Discord-issued one (same Rsvp row, same
  `RSVP_CHANNEL_ID` post). T-90 cutoff enforced (409); revokes are
  unaffected and pass through.

  **S6 bucket invariant — revoke + re-RSVP promotes back.** A revoke
  demotes UNASSIGNED → WONTASSIGN via
  [`buckets.demote_on_revoke`](../app/domain/buckets.py); a subsequent
  hard/soft RSVP now calls
  [`buckets.promote_from_wontassign`](../app/domain/buckets.py) FIRST,
  then falls back to `ensure_placed`. Net: the user lands back in main
  UNASSIGNED instead of stranded with a fresh RSVP in WONTASSIGN.
  Party / walk-in / volunteers placements are untouched (staff intent
  wins everywhere except WONTASSIGN, where the user's explicit re-RSVP
  is treated as the stronger signal).

  **S6 `wont_reason` distinguishes revoke from staff sit-out.** When a
  player is in WONTASSIGN AND a `Rsvp.revoked_at`-set row exists for
  the active event,
  [`snapshot._build_board_block`](../app/domain/snapshot.py) emits
  `wont_reason: "RSVP retracted"` instead of the generic
  `BUCKET_LABEL[WONTASSIGN]` ("Sitting out"). Vetsmod's `/wv anni`
  render is `wont_reason`-driven so the new string flows through
  without client changes.

- `POST /api/internal/anni-party-observation` (S7) — vetsmod back-report
  for `ONLINE_PARTY` corroboration. Body
  `{observer_mc_uuid, party_member_usernames, leader_username, world}`.
  `observer_mc_uuid` is stamped by temp-server from the authenticated
  session (never trusted from the frame body). Names are resolved here
  via [`AppState.resolve_uuid`](../app/services/state.py) (roster cache
  → legacy alias fallback) and written into
  `state.party_leader_by_uuid` for the presence classifier's
  `ONLINE_PARTY` upgrade. Unresolvable leader = no-op (`{resolved: 0}`);
  unresolvable members are dropped individually; the observer's session
  UUID always lands in the dict (authoritative fallback for brand-new
  members). Entries are TTL-gated (60 s) by `presence_poller` so a
  vetsmod disconnect mid-window doesn't pin the user to yellow forever.

temp-server's `app/services/anni_snapshot_poller.py` polls these on an
adaptive cadence (10s in the T-2h..T+30m hot window, 300s otherwise),
diffs per UUID, and pushes per-uuid `anni_state` WS frames to the
matching vetsmod client. vetsmod also issues `anni_query` over WS for the
on-demand pull (e.g. an `other`-tier user invoking `/wv anni`).

Subsequent stages (S2–S6) added richer `/wv anni` + anni-motd render,
passive/aggressive mode (boss bar, outlines, waypoints, chat alerts), and
in-game `/wv anni rsvp`. S7 adds party back-report via
`POST /api/internal/anni-party-observation` (replaces the legacy
vetsmod-tier gate with an organiser-presence gate).

See `.claude/snapshot_integration.md` for the full snapshot schema and the
upgrade-coordination story; the spec is in the vetsmod-fishbot integration
plan under `.claude/ephemeral/`.

## Auth model — intentionally LOW-TRUST (not a security boundary)
Web login is IGN + *optional* password (first set sticks; staff-resettable; no
email, no real verification). This is a deliberate coordination-tool choice
from the spec ("intentionally minimal friction"). Treat it as such everywhere:
no destructive action is exposed to anonymous/user sessions; staff/admin
actions sit behind the staff/admin password. Do not "harden" this into real
authn — that would contradict the spec. fishbot identity *is* trustworthy
(comes from dazebot's real Discord↔MC link).
