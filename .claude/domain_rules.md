# Domain rules

All encoded in `app/constants.py` (data) + `app/domain/*` (logic, pure &
unit-tested). No FastAPI/discord imports in either.

## Roles & colours (spec.md [^5]/[^6])
ONE shared palette (`constants.STYLES`, keyed by `PaletteColor`) backs the
role background, the status border **and** the RSVP icon. Roles and statuses
used to be *paired* one-to-one on the same entry; that is gone, replaced by
two disjoint tonal families:

| Family | Entries | Used by |
|---|---|---|
| **neon** | RED, YELLOW, GREEN, BLUE, CYAN, MAGENTA | the role chips **and** the status borders |
| **mid-tone** | TEAL, LIME, BRICK, ORCHID | the RSVP tickets |
| neutral | GREY | unassigned role, unknown status |

| Colour  | Default   | CB (Okabe-Ito)   | Used for            |
|---------|-----------|------------------|---------------------|
| RED     | `#ff0000` | vermillion       | primary / revoked   |
| YELLOW  | `#fffb00` | yellow           | secondary / soft    |
| GREEN   | `#15ff00` | bluish green     | healer / hard       |
| BLUE    | `#0400ff` | blue             | tank                |
| CYAN    | `#00e1ff` | black            | fill                |
| MAGENTA | `#ff00dd` | reddish purple   | tertiary            |
| TEAL    | `#37c5c8` | sky blue         | hard RSVP           |
| LIME    | `#93bd42` | yellow           | walk-in             |
| BRICK   | `#c93e36` | vermillion       | retracted           |
| ORCHID  | `#c13eab` | reddish purple   | soft RSVP           |
| GREY    | `#888888` | grey             | unassigned/unknown  |

**Statuses re-use role hues on purpose** — in-party is FILL's aqua, on-world
is HEALER's green, elsewhere is SECONDARY's sun-gold, offline is PRIMARY's
red. It is a mnemonic: the board is already dense, and a status palette an
organiser has to learn separately is one more thing to learn. Safe because a
role and a status never share a channel — a role is a card background or a
glyph swatch, a status is the border around the whole card.

(This inverts an earlier arrangement in which statuses had the mid-tone set
to themselves *precisely* so they could not be confused with roles. What
changed is the judgement, not the constraint: a deliberate echo teaches
faster than an unrelated palette avoids confusion.)

The RSVP ticket keeps its own family, because unlike the other two it is an
object sitting ON the card rather than a colouring of part of it.

Keeping the families disjoint means a role hue and a status hue can never be
mistaken for each other on a card, which the old shared-entry scheme could not
promise. The status ramp is picked as a *set* — cyan → green → yellow-green →
red → orchid walks the wheel one way, so the six borders read as one scale.
LIME is the "yellow between orange and green" step: a true yellow-green,
which is what sits between them on the wheel.

CB hues repeat across the families (LIME and YELLOW both land on yellow,
BRICK and RED both on vermillion, and so on). That is deliberate: a role is a
*background or glyph swatch*, a status is a *border*, and an RSVP ticket is an
*object on the card*, so none of them ever has to be told apart from another.
What must hold — and is enforced by `test_colourblind.py` in **both**
palettes — is that the STATUS hues stay mutually distinct, since they share
one channel in one place.

Each `STYLES` entry has `color` (default), `light`/`dark` (legible surfaces
for BLACK/WHITE text) and `cb` (Okabe-Ito, used under `body.cb`).
`ROLE_STYLES`/`STATUS_STYLES`/`RSVP_STYLES` only attach the glyph/icon +
label (+ border pattern for statuses) to a `STYLES` entry, so colour is never
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

## Reliability (`domain/reliability.py`)
How far staff can count on a player in one role. **Derived, never
declared, and staff-only** — it replaced the self-assessed "build quality"
field; players neither fill it in nor see it (not on their dashboard, not in
the Discord role lookup). Shown as a Low/Moderate/High level on the board's
capability popover and the staff roles page.

In points: start at 3 if confidence is HIGH, else 0; **+1 per win** in the
role (`success_count`); **−3 per penalised setback**. MODERATE at 3+ points;
HIGH needs **10+ wins and 10+ points**; else LOW. So new players start Low
(Moderate with high confidence), and nobody reaches High on confidence alone.

Setbacks (`Setback`, written by the grace-wipe): a LOSS in this role, or a
MISSED hard RSVP (counts against every role). Taken in date order, one is
**free** if it is among the first two ever, or the first in its calendar
month (UTC). So nobody is penalised before their third, three in one month
penalises the third, and one bad night a month never costs anything.

| Record (confidence) | Reliability |
|---|---|
| new (low / moderate) | Low |
| new (high) | Moderate |
| 3 wins (moderate) | Moderate |
| 10 wins | High |
| 10 wins, 3 losses in one month (moderate) | Moderate (10 − 3) |
| 13 wins, 3 losses in one month (moderate) | High (13 − 3) |
| 10 wins, one loss a month for 6 months | High (all free) |

**Staff override** (`/staff/roles` → Edit → "Reliability override"): restarts
the score on the chosen tier's line *now* — Low 0, Moderate 3, High 10
points — replacing the confidence start and everything before it. From then
on only wins and setbacks **recorded after** the restart move it, each judged
as it otherwise would be (the free allowance still runs over the whole
history, so a restart doesn't hand out two fresh free setbacks). A High
restart vouches for the 10-win gate; Low/Moderate still need 10 lifetime
wins to climb to High. The select defaults to "No change" so re-saving never
re-bases; "Clear override" returns to the derivation from the whole record.
The roles page marks an overridden capability "staff-set".

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

Absence is ONE status. `OFFLINE_GONE` used to be a second one and is now a
badge (see below), because "was here earlier" is *history*, not a degree of
presence — as a sixth border it overwrote the present rather than adding to
it, so an organiser could not see "not here" and "and they were, ten minutes
ago" at the same time.
- OFFLINE (not here right now, whether or not we saw them earlier):
  - Staff see: PRIMARY red (`#ff0000`) border outlining user object in staff dashboard.
  - Users see: Red bar under relevant module in user dashboard. It flashes
    immediately for someone who was here and left (they have already shown
    they can make it, so the countdown can't soften that), else from T-20m
    with a hard RSVP and T-45m with a soft one — and **never** for someone
    who never RSVP'd, since they never said they were coming. The bar copy
    names whichever fact is the more specific: the history if there is one
    ("We saw you around…"), otherwise the promise ("You hard-RSVP'd but
    aren't online yet").
- ONLINE_ELSEWHERE (online, but in a queue or otherwise not on their assigned party's world. Or, they haven't been assigned to a party yet):
  - Staff see: SECONDARY sun-gold (`#fffb00`) border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard, switches to a yellow bar when their world has been announced.
- ONLINE_WORLD (online, in the correct world, but not in their assigned party)
  - Staff see: HEALER green (`#15ff00`) border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard, switches to a yellow bar when their party has been created.
- ONLINE_PARTY (online, in the correct world, in their assigned party)
  - Staff see: FILL aqua (`#00e1ff`) border outlining user object in staff dashboard.
  - Users see: Green bar under relevant module in user dashboard.
- UNKNOWN: (The user has their API disabled and we are not comfortable in our aproximations of if they are online or offline. We have several sources (world shift and vetsmod reporting -- see wv list), but if we are unsure, we can use this list their status as unconfirmable).
  - Staff see: GREY border outlining user object in staff dashboard.
  - Users see: Yellow bar under relevant object in user dashboard indicating that their API settings prevent us from knowing their status and we are unable to surmise it.

**NOTE THAT** users in queues (reported as `queued` in online-merge, see the /wv list implementation for reference (i.e. queued on /v1/outbound/list)) are `ONLINE_ELSEWHERE`, not `OFFLINE_*`. Anni is a very queue-intensive event, so this will likely be encountered *a lot*

## RSVP axis (`constants.RsvpState`, `domain/rsvp.state_of`)
The second, independent channel on every person card — the icon left of the
avatar. Derived per (event, player) from the `Rsvp` row, never stored
separately:

Every state is a **ticket** — the differences are what is on it, whether the
outline is solid or dotted, and whether the silhouette is whole or torn:

| State | Ticket | Colour | Means |
|---|---|---|---|
| `NONE` | dashed outline, exclamation | LIME (yellow-green) | no `Rsvp` row — a walk-in, they never declared |
|  |  |  | *(a fourth axis, the hourglass over the avatar, marks a late arrival — see below; any RSVP state can carry it)* |
| `HARD` | tick | TEAL (green-blue) | live row, `notice=RSVP_HARD` |
| `SOFT` | clock | ORCHID (red-purple) | live row, `notice=RSVP_SOFT` |
| `REVOKED` | blank, torn in half | BRICK (red-orange) | row soft-deleted (`revoked_at` set) |

Drawn as inline SVG in `templates/macros/icons.html`, hand-pathed on a 24×20
grid to match the `fa-slab-duo fa-regular fa-ticket` reference (~1.25:1,
generous corners, **two** semicircular bites per end). The app vendors every
asset, and hand paths dodge icon-font licensing.

**The edge is what makes it a ticket.** Two bites per end, not one — flat,
notch, longer flat, notch, flat. A single central notch reads as a waist: the
silhouette pinches in the middle and the thing looks like a spool. Two
shallower ones read as perforations, which is the whole visual argument that
this is a ticket. Notch depth is bounded by the outline weight (ink grows
outward from the path, so a bite much shallower than the line is
half-swallowed and stops registering), which is why the rings run lighter than
a true slab — the silhouette matters more than the weight. For the same
reason the walk-in ticket is **dashed, not dotted**: round dots at this scale
swallowed the notches and corners, and a ticket you cannot see the edge of is
not recognisably a ticket.

**Three layers, one hue.** The wrapper sets `color` to the hue's `-light`
tint (the linework) and `--tk-fill` to its `-dark` shade (the ticket's body).
The dark is a **fill on the ticket path**, never a background behind the icon,
so it stops at the ticket's edge instead of boxing it in — which is also why
it has to arrive as a custom property read by a CSS rule, since `var()` is
inert inside an SVG presentation attribute. The silhouette is then stroked
three times over the same path, widest first (white, colour, black), and the
body fill is drawn **last**: a centred stroke straddles its path, so the fill
clips the inner half of all three rings and what survives reads outward from
the body as black hairline → coloured slab → white hairline. SVG has no inner
border and no way to offset a stroke to one side of its path; the ring stack
is how you get an asymmetric edge out of it.

Since the ticket brings its own dark body, one icon file reads identically on
a dark role-coloured card and on the pale legend — no keyline hack needed, and
the linework can stay thin enough for the mark on it to register.

`REVOKED` deliberately outranks the stored notice: once someone pulls out,
"they retracted" is the fact staff act on, not what they had said before.
`rsvp.states_by_uuid(event)` is the one *unfiltered* Rsvp read in the codebase
(everything else filters `revoked_at__isnull=True`) precisely because a
revoked row is a state to render, not an absence to hide.

## Board sub-buckets (`web/board_view`)
Unassigned has three lanes and every party has two. Only **one** of them is
stored (`BoardPlacement.is_walkin` — "never declared"); the soft-RSVP lanes
are derived on every render:

- Unassigned: `on_time` → `soft` → `walkin`.
- Each party: its members → `offline_soft`.

The two derived lanes deliberately use **different** predicates:

| Lane | Predicate | Why |
|---|---|---|
| Unassigned `soft` | `is_soft` — soft RSVP, online or not | Unassigned is a queue an organiser is placing *from*, so they want every maybe together |
| Party `offline_soft` | `is_offline_soft` — soft RSVP **and** `OFFLINE` | A party slot is already allocated, so the only ones worth flagging are the maybes who haven't turned up |

`UNKNOWN` is excluded from the party predicate — unconfirmable is not absent,
and parking someone in the "don't count on them" lane on a guess is the
fabrication the spec forbids.

Deriving rather than storing is what makes the **auto-promote** work: a soft
RSVP in a party who logs on is pulled back into the party proper on the next
presence tick, with nobody dragging them. A stored flag would go stale, and
party placements have no lane flags at all. Consequences worth knowing:
- A lane's **dropzone targets the same container as the lane above it** (the
  party, or the main Unassigned lane). Dragging a card in or out of it is a
  no-op that re-derives on the next render — it is a *view* of a container,
  not a container.
- A party's `count` (the N/10 header) spans both lanes — an offline soft RSVP
  is still occupying a slot.

**There is no LATE lane.** Lateness stopped being a partition of the queue
when its threshold started sliding with the RSVP (below); it is a fact about
one card, and renders as the hourglass over that card's avatar.

## Avatar stamps: gone and late
Two facts are *history* rather than presence, so neither is a status — each
is a stamp on the person card's avatar. Both can be true; **at most one is
drawn, and gone outranks late**. "They are not here" is what an organiser
acts on; "they were late getting here" is water under the bridge. When they
log back on the `?` clears and the hourglass reappears on its own, because
gone is derived rather than stored — there is no state to unwind.

- **gone** (`board_view`'s `is_gone`, a white `?`) — status is `OFFLINE`
  **and** the uuid is in `AppState.seen_online_uuids`, the add-only set
  `presence_poller` accumulates and the grace-wipe clears. Derived per
  render, so it tracks live presence exactly.
- **late** (`BoardPlacement.is_late`, a white hourglass) — see below. Stored
  at insert, because it is a fact about a moment that has passed.

Both render as a translucent black disc with an opaque white glyph, centred
on the face — see `colourblind.md` for why that treatment and not `opacity`.

## Late arrivals (`constants.LATE_ARRIVAL_SECONDS`, `hot_window.is_late_arrival`)
Whether an arrival counts as late depends on what they had promised — someone
who committed has earned more slack than someone nobody was told about:

| RSVP | Late if they arrive after |
|---|---|
| hard | T-15 |
| soft | T-35 |
| walk-in (none) | T-50 |
| retracted | n/a — never late |

Evaluated **once**, when the card lands on the board (`buckets.ensure_placed`
/ `add_walkin` — never the caller, so the three insert paths can't drift), and
stored on `BoardPlacement.is_late`. It is a fact about when someone turned up,
so no move touches it: dragging a latecomer into a party must not launder the
hourglass away. (This is also why `buckets.move` has no `is_late` parameter at
all — while it was a lane, every move supplied one, and a REST move defaulting
it to False would have silently cleared the mark.)

`hot_window.is_late_bucket` is a *different* thing that kept its name: the
T-60 switch behind the board's `live` pill label.

## Retraction (`buckets.demote_on_revoke`)
Revoking an RSVP moves the card to **Sitting out** from wherever it currently
sits, a party included. Re-RSVPing undoes it (`promote_from_wontassign` pulls
them back to the main Unassigned lane), so it is never a dead end.

This used to only demote out of Unassigned, on the reasoning that staff intent
should beat a user action. That was backwards for the case that matters:
someone sitting in a party who pulls out is exactly when an organiser needs
the seat freed, and a card left in place was easy to miss.

## Lifecycle & grace-wipe (`services/lifecycle_task.py`)
`stamp` future → active event. now>stamp & ≤stamp+2h → grace (board read-only
except per-party result + stage). now>stamp+2h → wipe in ONE transaction:
snapshot results, increment `success_count` for WIN **party members only** —
anyone left in a bucket (Unassigned / Volunteers / Sitting-out) earns nothing
even if their card carries an assigned role, since a role is routinely set
before (or left set after) a card is dragged into a party — record reliability
setbacks (below), delete
`BoardPlacement`/`Rsvp` for the event, mark `wiped_at`+`is_active=False`,
broadcast `BOARD_WIPE`. `RoleCapability`/`AnniPlayer` persist. A new/changed
future stamp updates the active event (re-announcement), not a duplicate.

Setbacks at the wipe: **LOSS** for exactly the members the win credit would
have covered (party members, core role, holding that capability) in parties
marked LOSS — LAG is never a setback, TBD is a win. **MISSED** for a
non-revoked hard RSVP whose player never sat in a party and was never seen
online in the hot window (`Rsvp.seen_online_at`). Never fabricated: a player
whose API is hidden is exempt (unconfirmable, not absent), and if no RSVP
was stamped at all the presence poller wasn't running, so none are recorded.

## API-disabled inference (`services/api_disabled.py`)
Epoch `last_online` ⇒ disabled. Confirm presence via the online-merge source
first (vetsmod connection shows them regardless of WAPI privacy), then a slow
between-tick `/v3/player` `lastSeen`-server-change probe (dazebot purgelist-style).
Neither style is fully reliable though: Unconfirmable ⇒ `UNKNOWN`.
