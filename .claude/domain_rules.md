# Domain rules

All encoded in `app/constants.py` (data) + `app/domain/*` (logic, pure &
unit-tested). No FastAPI/discord imports in either.

## Roles & colours (spec.md [^5]/[^6])
ONE shared palette (`constants.STYLES`, keyed by `PaletteColor`) backs the
role background, the status border **and** the RSVP badge. The three families
each pick their own entries; roles and statuses used to be *paired* one-to-one
on the same entry and no longer are (the status ramp needed ORANGE and PINK,
which no role uses):

| Colour  | Role       | Status border    | RSVP badge |
|---------|------------|------------------|------------|
| RED     | primary    | offline          | revoked    |
| ORANGE  | —          | online-elsewhere | —          |
| YELLOW  | secondary  | —                | soft       |
| GREEN   | healer     | online-world     | hard       |
| BLUE    | tank       | online-party     | —          |
| CYAN    | fill       | —                | —          |
| MAGENTA | tertiary   | —                | —          |
| PINK    | —          | offline-gone     | —          |
| GREY    | unassigned | unknown          | none       |

Each `STYLES` entry has `color` (default), `light`/`dark` (legible surfaces
for BLACK/WHITE text) and `cb` (Okabe-Ito, used under `body.cb`). PINK's `cb`
is the same reddish-purple as MAGENTA's on purpose: MAGENTA is only ever a
role *background* and PINK only ever a status *border*, so the two never have
to be told apart, and the only unclaimed Okabe-Ito hue left (sky-blue) sits
too close to the BLUE the status ramp now uses.
`ROLE_STYLES`/`STATUS_STYLES`/`RSVP_STYLES` only attach the glyph + label
(+ border pattern for statuses) to a `STYLES` entry, so colour is never
load-bearing — see `colourblind.md`. Capability rows use the 5 core roles;
FILL is assignable/colourable only.

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

## Preferred regions (`domain/regions.py`)
A user's preferred play region(s): a self-set multi-select over the **MaxMind
GeoIP2 continent codes** — `ContinentCode` in `constants.py` holds all seven
verbatim (`AF`/`AN`/`AS`/`EU`/`NA`/`OC`/`SA`); `CONTINENT_LABEL` is the
full-name display map, `CONTINENT_GLYPH` the per-code globe emoji and
`CONTINENT_ORDER` the canonical order. Stored on `AnniPlayer.preferred_regions`
as a CSV of codes ("" = no preference → shown as "Any region"), the same
readable-string rationale as the other string-stored enums. `domain/regions.py`
is the only place that parses it: `parse()` is deliberately tolerant
(trims/upcases, drops unknown tokens, never raises — a hand-edited/stale/
since-disabled value can't break a dashboard), `to_csv()` re-emits the
canonical de-duped, order-stable CSV, `coerce()` turns an iterable of code
strings (e.g. the setting) into codes, `choices(enabled)`/`options()` feed the
picker, `restrict()` clamps a save to the enabled set, `labelled()` shapes
`[{code,label,glyph}]` for the read pills. It is **not** an
eligibility/priority input — purely informational context for the user and for
organisers balancing parties across regions.

**Enabled set (the "toggle").** Wynn only runs server proxies for some
continents (today AS/EU/NA). The picker offers **only the enabled set** —
`settings.enabled_regions` (`ENABLED_REGIONS` env, default
`DEFAULT_ENABLED_REGIONS` = AS/EU/NA) — so users aren't offered regions Wynn
can't host, and a save is `restrict()`-clamped to it (a crafted POST can't
store a disabled region). It's config-only: widen `ENABLED_REGIONS` as Wynn
adds proxies, no redeploy/code change (the capability is in place; there's no
in-app staff UI for it in Phase 1 by scope). A region **outside** the enabled
set is still a valid `ContinentCode`: an already-stored one keeps parsing and
**still renders** in the read pills (it just won't be re-offered, and the next
save drops it). Empty `ENABLED_REGIONS` ⇒ no options ⇒ the picker degrades to
a "no play regions enabled" note.

Each region renders as `CONTINENT_GLYPH` + code + full-name `title` (🌍
EU/AF, 🌎 NA/SA, 🌏 AS/OC, 🇦🇶 AN; `ANY_REGION_GLYPH` 🌐 for the no-preference
pill). Surfaced on the user General module — read pills + an "Edit Regions."
button on the Membership line (`user/_regions.html`, root `#regions`; no
caption — the button label is the only affordance text); editing is a
**popup modal**
(`user/_regions_modal.html` → `#modal-mount`, mirroring the capability
modal) whose save swaps the `#regions` block back in (dashboard JS then
clears the mount). Keeping the picker off-page is deliberate: an inline
expander reflowed the card. Also on the Phase-2 staff board person card
(`macros/pills.html` `regions`) — textual (glyph + code + title), so it
needs no colourblind handling.

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
`Party.stage`, `Rsvp.notice`, `was_online` (seen online at any point this
anni), countdown (stamp−now), api-disabled inference. Outputs a
`PresenceStatus` + escalating bottom-bar text.

**The status is presence ONLY — it does not encode the RSVP.** It used to:
the offline branch was split `OFFLINE_HARD`/`OFFLINE_SOFT`. The RSVP is now
its own board channel (`RsvpState`, below), so the border answers exactly one
question — *where are they right now* — and the two facts can be read
together instead of one hiding the other. What the notice still drives is how
loudly the **user dashboard bar** nags someone who isn't on yet.

An offline person is exactly one of `OFFLINE_GONE` / `OFFLINE`:
- OFFLINE_GONE (was here at some point this anni, no longer here):
  - Staff see: PINK border outlining user object in staff dashboard.
  - Users see: subtly flashing bar under relevant module in user dashboard.
  - The "was here" half is real history now, not inferred from the absence of
    an RSVP: `presence_poller` accumulates every uuid it sees online into
    `AppState.seen_online_uuids` and feeds it back as `was_online`. The
    grace-wipe clears the set, so it never leaks across events.
- OFFLINE (not here, and we have not seen them tonight):
  - Staff see: RED border outlining user object in staff dashboard.
  - Users see: Red bar under relevant module in user dashboard. It starts
    flashing at T-20m with a hard RSVP, T-45m with a soft one, and **never**
    for someone who never RSVP'd — they never said they were coming, so there
    is nothing to be late for. The bar copy still names the promise ("You
    hard-RSVP'd but aren't online yet").
- ONLINE_ELSEWHERE (online, but in a queue or otherwise not on their assigned party's world. Or, they haven't been assigned to a party yet):
  - Staff see: ORANGE border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard, switches to a yellow bar when their world has been announced.
- ONLINE_WORLD (online, in the correct world, but not in their assigned party)
  - Staff see: GREEN border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard, switches to a yellow bar when their party has been created.
- ONLINE_PARTY (online, in the correct world, in their assigned party)
  - Staff see: BLUE border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard.
- UNKNOWN: (The user has their API disabled and we are not comfortable in our aproximations of if they are online or offline. We have several sources (world shift and vetsmod reporting -- see wv list), but if we are unsure, we can use this list their status as unconfirmable).
  - Staff see: GREY border outlining user object in staff dashboard.
  - Users see: Yellow bar under relevant object in user dashboard indicating that their API settings prevent us from knowing their status and we are unable to surmise it.

**NOTE THAT** users in queues (reported as `queued` in online-merge, see the /wv list implementation for reference (i.e. queued on /v1/outbound/list)) are `ONLINE_ELSEWHERE`, not `OFFLINE_*`. Anni is a very queue-intensive event, so this will likely be encountered *a lot*

## RSVP axis (`constants.RsvpState`, `domain/rsvp.state_of`)
The second, independent channel on every person card — the badge left of the
avatar. Derived per (event, player) from the `Rsvp` row, never stored
separately:

| State | Glyph | Colour | Means |
|---|---|---|---|
| `NONE` | `W` | GREY | no `Rsvp` row — a walk-in, they never declared |
| `HARD` | `✓` | GREEN | live row, `notice=RSVP_HARD` |
| `SOFT` | `~` | YELLOW | live row, `notice=RSVP_SOFT` |
| `REVOKED` | `✕` | RED | row soft-deleted (`revoked_at` set) |

`REVOKED` deliberately outranks the stored notice: once someone pulls out,
"they retracted" is the fact staff act on, not what they had said before.
`rsvp.states_by_uuid(event)` is the one *unfiltered* Rsvp read in the codebase
(everything else filters `revoked_at__isnull=True`) precisely because a
revoked row is a state to render, not an absence to hide.

## Board sub-buckets (`web/board_view`)
Unassigned has four lanes and every party has two. Three of them are
**stored** (`BoardPlacement.is_late` / `.is_walkin` — how someone arrived);
one is **derived** on every render:

- Unassigned: `on_time` → `offline_soft` → `walkin` → `late`.
- Each party: its members → `offline_soft`.

`board_view.is_offline_soft` is the whole rule: soft RSVP **and** a presence
status of `OFFLINE`/`OFFLINE_GONE`. `UNKNOWN` is excluded — unconfirmable is
not absent, and parking someone in the "don't count on them" lane on a guess
is the fabrication the spec forbids.

It has to be derived rather than a fourth stored flag: it tracks live
presence, so a soft RSVP logging on must leave the lane on the next presence
tick without anyone dragging them, and party placements have no lane flags at
all. Consequences worth knowing:
- The lane's **dropzone targets the same container as the lane above it** (the
  party, or the main Unassigned lane). Dragging a card in or out of it is a
  no-op that re-derives on the next render — it is a *view* of a container,
  not a container.
- It is carved out of the **main lane only**. Walk-ins by definition never
  RSVP'd so they cannot qualify; LATE is a provenance marker worth keeping
  whole, so a late soft-RSVP stays in LATE.
- A party's `count` (the N/10 header) spans both lanes — an offline soft RSVP
  is still occupying a slot.

## Lifecycle & grace-wipe (`services/lifecycle_task.py`)
`stamp` future → active event. now>stamp & ≤stamp+2h → grace (board read-only
except per-party result + stage). now>stamp+2h → wipe in ONE transaction:
snapshot results, increment `success_count` for WIN **party members only** —
anyone left in a bucket (Unassigned / Volunteers / Sitting-out) earns nothing
even if their card carries an assigned role, since a role is routinely set
before (or left set after) a card is dragged into a party — delete
`BoardPlacement`/`Rsvp` for the event, mark `wiped_at`+`is_active=False`,
broadcast `BOARD_WIPE`. `RoleCapability`/`AnniPlayer` persist. A new/changed
future stamp updates the active event (re-announcement), not a duplicate.

## API-disabled inference (`services/api_disabled.py`)
Epoch `last_online` ⇒ disabled. Confirm presence via the online-merge source
first (vetsmod connection shows them regardless of WAPI privacy), then a slow
between-tick `/v3/player` `lastSeen`-server-change probe (dazebot purgelist-style).
Neither style is fully reliable though: Unconfirmable ⇒ `UNKNOWN`.
