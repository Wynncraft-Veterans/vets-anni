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

## Wynncraft API — OWN token, separate ratelimit bucket (mandated)
`services/wapi.py` is the only place that sends `WAPI_TOKEN`. Honour
`RateLimit-*` headers, back off on 429 (port of dazebot's Requestor). We spend
the token only on: `/v3/guild/Returners` online, `/v3/item/search/{q}` (weapons
catalog, ITEMS bucket, 1 h cache), and the slow api-disabled `/v3/player/{uuid}`
probe. Heavy reads come from api.wynnvets.org instead.

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
