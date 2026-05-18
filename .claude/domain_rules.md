# Domain rules

All encoded in `app/constants.py` (data) + `app/domain/*` (logic, pure &
unit-tested). No FastAPI/discord imports in either.

## Roles & colours (spec.md [^5]/[^6])
ONE shared palette (`constants.STYLES`, keyed by `PaletteColor`) backs **both**
the role background and the status border — a role and its paired status are
the *same* colour entry:

| Colour | Role      | Status border    |
|--------|-----------|------------------|
| RED    | primary   | offline-gone     |
| YELLOW | secondary | offline-soft     |
| GREEN  | healer    | offline-hard     |
| BLUE   | tank      | online-elsewhere |
| CYAN   | fill      | online-world     |
| MAGENTA| tertiary  | online-party     |
| GREY   | unassigned| unknown          |

Each `STYLES` entry has `color` (default), `light`/`dark` (legible surfaces
for BLACK/WHITE text) and `cb` (Okabe-Ito, used under `body.cb`).
`ROLE_STYLES`/`STATUS_STYLES` only attach the glyph + label (+ border pattern
for statuses) to a `STYLES` entry, so colour is never load-bearing — see
`colourblind.md`. Capability rows use the 5 core roles; FILL is
assignable/colourable only.

## Membership (`domain/membership.py`)
`MEMBER` = in guild `RETURNERS_GUILD_NAME`; `COMMUNITY` = guildless; `ALLY` =
guild whose **tag** is in the configured `ALLY_GUILD_TAGS` list (matched
**exactly** — Wynncraft guild tags are case-sensitive; seeded
`SSNE,TCM,VSI,BELL`); `OTHER` = any other guild. `WAITLIST`/`HONOURARY`
come from dazebot tier resolution (via the anni-identity endpoint). Priority
order MEMBER>WAITLIST>HONOURARY>COMMUNITY>ALLY>OTHER (`MEMBERSHIP_PRIORITY`).

Wynncraft guild **tags _and_ names are NOT unique** (old-API quirk; e.g. `TCM`
= our ally *Team CM* **and** the inactive *Moments*). Tag-keying is accepted
anyway because COMMUNITY/ALLY/OTHER are only evaluated for **RSVP'd** players
and the colliding guilds are inactive, so an active player's resolved guild is
the right one. **Guild UUID is the only definitive disambiguator** — if a
collision ever goes active, re-key `ALLY_GUILD_TAGS` to UUIDs (not names).

## Capability (`domain/capability.py`)
Core = ≥1 `RoleCapability`; Fill = none → red warning bar. The add-capability
UI quotes `ROLE_GUIDANCE` (requirements + gameplay/builds links to
wynnvets.org/docs/guild/anni).

A capability holds **multiple weapons** (e.g. a primary-capable user on both
`Labyrinth` *and* `Revolution`). Constraints, enforced at write time:
(1) every weapon must be real — validated against the cached WAPI item catalog;
(2) at most `MAX_WEAPONS_PER_CAPABILITY` (= 3) weapons **per role** — 3 for
primary and a separate 3 for secondary is fine. Modelled as N
`RoleCapabilityWeapon` rows under one `(player, role)` `RoleCapability`.

## Attendance likelihood (`domain/attendance.py`)
`ATTENDANCE_TABLE` is the published priority table as ordered rules
(membership × Core/Fill × notice → an **exact `pct`**). First match wins; an
N/A cell has no row, so `evaluate()` → `None`. The raw percentage is
**internal and never shown to users** (an exact number invites
rules-lawyering): `meta()` collapses it via `LIKELIHOOD_BANDS` into one
visible band (1..6) — `Most Unlikely`…`Most Likely`. An off-table/N/A cell is
treated as 0% → still `Most Unlikely` (there is **no** separate "not
prioritised" level). The dashboard bar's fill width + colour are derived from
the *band*, not the percent, so the number isn't recoverable from the UI.
`AttendanceNotice`
precedence: `ATTEND_EARLY` > `RSVP_HARD` > `RSVP_SOFT` > `ATTEND_LATE`. Only
`RSVP_HARD`/`RSVP_SOFT` are stored (on `Rsvp.notice`); `ATTEND_EARLY`/
`ATTEND_LATE` are derived.

For a user with no RSVP, the effective notice is **projected from the
countdown**: if `anni − now ≥ EARLY_NOTICE_CUTOFF_SECONDS` (60 min) → treat as
`ATTEND_EARLY`, else `ATTEND_LATE`. The dashboard frames it conditionally
("assuming you log on now, you'd be EARLY/LATE → likelihood X").
Board members always have a real notice.

The projection only applies to `_PROJECTABLE_TIERS` (the Vets tiers — the ones
with an Early/Late cell; derived from the table so it can't drift). For
Community/Ally/Other, Early/Late are an *impossible* state — we can't track a
guildless/ally/other player without an RSVP. So the projection never overrides
or manufactures a notice for them: `effective_notice` returns `None` for a
non-trackable tier with no RSVP, so such a user falls to the lowest band
(`Most Unlikely`) until they RSVP (they have no "just show up" option).

## Presence state machine (`domain/presence.py`)
Inputs: online-merge membership, assigned `Party.world` vs current server,
`Party.stage`, `Rsvp.notice`, countdown (stamp−now), api-disabled inference.
Outputs a `PresenceStatus` + escalating bottom-bar text:

An offline person is exactly one of `OFFLINE_GONE` / `OFFLINE_HARD` /
`OFFLINE_SOFT`; once gone, 1hr-early vs late is tracked elsewhere and irrelevant).
- OFFLINE_GONE (was here <= T-60m, no longer here):
  - Staff see: RED border outlining user object in staff dashboard.
  - Users see: subtly flashing bar under relevant module in user dashboard.
- OFFLINE_HARD: (hard rsvp'd, but is not here (yet)):
  - Staff see: GREEN border outlining user object in staff dashboard.
  - Users see: Red bar under relevant module in user dashboard, starts flashing T-20m.
- OFFLINE_SOFT: (soft rsvp'd, is not here (yet)):
  - Staff see: YELLOW border outlining user object in staff dashboard.
  - Users see: Red bar under relevant module in user dashboard, starts flashing T-45m.
- ONLINE_ELSEWHERE (online, but in a queue or otherwise not on their assigned party's world. Or, they haven't been assigned to a party yet):
  - Staff see: BLUE border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard, switches to a yellow bar when their world has been announced.
- ONLINE_WORLD (online, in the correct world, but not in their assigned party)
  - Staff see: CYAN border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard, switches to a yellow bar when their party has been created.
- ONLINE_PARTY (online, in the correct world, in their assigned party)
  - Staff see: MAGENTA border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard.
- UNKNOWN: (The user has their API disabled and we are not comfortable in our aproximations of if they are online or offline. We have several sources (world shift and vetsmod reporting -- see wv list), but if we are unsure, we can use this list their status as unconfirmable).
  - Staff see: GREY border outlining user object in staff dashboard.
  - Users see: Yellow bar under relevant object in user dashboard indicating that their API settings prevent us from knowing their status and we are unable to surmise it.

**NOTE THAT** users in queues (reported as `queued` in online-merge, see the /wv list implementation for reference (i.e. queued on /v1/outbound/list)) are `ONLINE_ELSEWHERE`, not `OFFLINE_*`. Anni is a very queue-intensive event, so this will likely be encountered *a lot*

## Lifecycle & grace-wipe (`services/lifecycle_task.py`)
`stamp` future → active event. now>stamp & ≤stamp+2h → grace (board read-only
except per-party result + stage). now>stamp+2h → wipe in ONE transaction:
snapshot results, increment `success_count` for WIN parties, delete
`BoardPlacement`/`Rsvp` for the event, mark `wiped_at`+`is_active=False`,
broadcast `BOARD_WIPE`. `RoleCapability`/`AnniPlayer` persist. A new/changed
future stamp updates the active event (re-announcement), not a duplicate.

## API-disabled inference (`services/api_disabled.py`)
Epoch `last_online` ⇒ disabled. Confirm presence via the online-merge source
first (vetsmod connection shows them regardless of WAPI privacy), then a slow
between-tick `/v3/player` `lastSeen`-server-change probe (dazebot purgelist-style).
Neither style is fully reliable though: Unconfirmable ⇒ `UNKNOWN`.
