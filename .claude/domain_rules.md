# Domain rules

All encoded in `app/constants.py` (data) + `app/domain/*` (logic, pure &
unit-tested). No FastAPI/discord imports in either.

## Roles & colours (spec.md [^5]/[^6])
Roles: primary=red, secondary=yellow, tertiary=purple, healer=green,
tank=indigo, fill=cyan, unassigned=gray. Status borders: offline-gone=red,
offline-hard=green, offline-soft=yellow, online-elsewhere=blue,
online-world=indigo, online-party=purple, unknown=gray. Each role/status ALSO
carries a glyph + label (+ border pattern for statuses) so colour is never
load-bearing — see `colourblind.md`.
Capability rows use the 5 core roles; FILL is assignable/colourable only.

## Membership (`domain/membership.py`)
`MEMBER` = in guild `RETURNERS_GUILD_NAME`; `COMMUNITY` = guildless; `ALLY` =
configured `ALLY_GUILD_ID`; `OTHER` = any other guild. `WAITLIST`/`HONOURARY`
come from dazebot tier resolution (via the anni-identity endpoint). Priority
order MEMBER>WAITLIST>HONOURARY>COMMUNITY>ALLY>OTHER (`MEMBERSHIP_PRIORITY`).

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
(membership-set × Core/Fill × notice → `Likelihood`). First match wins;
`LIKELIHOOD_META` gives the bar % + label. Notice precedence:
ONE_HR_EARLY > HARD_RSVP > SOFT_RSVP > LATE/NONE.

## Presence state machine (`domain/presence.py`)
Inputs: online-merge membership, assigned `Party.world` vs current server,
`Party.stage`, `Rsvp.notice`, countdown (stamp−now), api-disabled inference.
Outputs a `PresenceStatus` + escalating bottom-bar text:

An offline person is exactly one of `OFFLINE_GONE` / `OFFLINE_HARD` /
`OFFLINE_SOFT` (no "offline, no RSVP" state — on-list-without-RSVP means
*here*; once gone, 1hr-early vs late is tracked elsewhere and irrelevant).
Borders are **always steady** — no status ever has a pulsing/flashing border
(e.g. on the staff board a gone user is a steady red border). The "flashing"
affordance is the **bottom bar** only.
- OFFLINE_GONE (offline — was here / never showed): steady red border;
  **bottom bar flashes "at risk" immediately**.
- OFFLINE_HARD: steady green border; safe until T-20m, then the bottom bar
  flashes "at risk".
- OFFLINE_SOFT: steady yellow border; safe until T-45m, then the bottom bar
  flashes "at risk".
- ONLINE_ELSEWHERE (wrong world): "move to world X" if announced, else green.
- ONLINE_WORLD (party world, not in party): "join party" if created, else green.
- ONLINE_PARTY (in the party): always green.
- UNKNOWN: API-disabled & unconfirmable — surfaced honestly, never "online".

**Queue rule (important — anni is queue-intensive):** a player the
online-merge source reports as `queued` (stuck in a Wynncraft server queue) is
*connecting*, not gone. Presence MUST NOT mark a queued player `OFFLINE_GONE`;
treat them as online/connecting. `/wv list` already shows queued users from the
same `/v1/outbound/list` `queued` flag, so mirroring that source keeps us
correct — see `constants.QUEUE_NEVER_OFFLINE_GONE`.

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
between-tick `/v3/player` `lastSeen`-server-change probe (purgelist-style).
Unconfirmable ⇒ `UNKNOWN`.
