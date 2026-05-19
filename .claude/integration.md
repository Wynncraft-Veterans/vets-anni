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

## vetsmod (App4, deferred)
New `AnniFetcher` + richer `/wv anni` reading vets-anni `GET /v1/anni/me`;
party-detection chat hook → `POST /v1/anni/party-report` (best-effort
corroboration, never authoritative); optional role glow via
`NametagAnimator`/`NametagMixin` reading `GET /v1/anni/roster-colours`, gated
by a VetsConfig toggle + CB palette.

## Auth model — intentionally LOW-TRUST (not a security boundary)
Web login is IGN + *optional* password (first set sticks; staff-resettable; no
email, no real verification). This is a deliberate coordination-tool choice
from the spec ("intentionally minimal friction"). Treat it as such everywhere:
no destructive action is exposed to anonymous/user sessions; staff/admin
actions sit behind the staff/admin password. Do not "harden" this into real
authn — that would contradict the spec. fishbot identity *is* trustworthy
(comes from dazebot's real Discord↔MC link).
