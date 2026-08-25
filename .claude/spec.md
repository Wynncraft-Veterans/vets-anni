# The Wynnvets Annihilation Stack — Authoritative Spec

> This is the authoritative product spec for vets-anni. Keep it updated when
> the requirements change; everything else (code, the other `.claude/*.md`)
> serves what is described here.

## Context

### Background

- [Wynnvets](https://wynnvets.org) is a community and in-game guild centred around the Wynncraft Minecraft server.
- [Annihilation](https://wynnvets.org/anni) is a twice-thrice weekly event hosted by Wynn, centring around a ten player boss fight.
- Given that Wynnvets is a large community full of players differing in capability and experience, a system has been developed over the years to streamline our efforts to ensure as many eligible players as possible are able to beat the boss whenever it shows up.
  - This includes spots for experienced players (core slots), but also spots to help players who have recently returned to the game make it through the fight (fill slots).
- Over time, various programs have been created to help support the operations of the above system. These supports have become too fragmented and are being formally consolidated as the vets-anni codebase.

### Environment

- This application will run on the vets-deploy timasca server.
- This application will include its own database and its own web app.
- This application will include its own discord bot named fishbot with prefix `\`.
- This application will integrate with the temporary-server, dazebot, and vetsmod codebases where and as needed.
- This application will run as anni.wynnvets.org, and will have its own vets-deploy stack.
- This application will be serviced with the vets-deploy manage tool

### Key Concepts

- The ORGANISING STAFF is the staff member who has been voluntold to host a specific day's parties. Usually exclusively in charge of figuring out how many parties we can support, assigning people to roles, etc.
- The HOSTING STAFF are the staff other than the organiser, supporting the organiser. Each party the organiser creates has an assigned staff host who creates the party, invites everyone, and manages it per assigned roles.
  - Upon being assigned a party, hosting staff find a world with enough slots, take out consumable resources from the guild, etc.
- CORE ANNI ROLES are archetypical positions necessary to a successful anni.
  - Effectiveness depends on experience playing and how refined one's role-specific build is.
    - Some weapons suit a role (guardian is great for tank); some synergise. Know what weapons people use.
    - Knowing how many annihilations someone has completed in a role indicates experience.
    - User self-attestation of build polish indicates build refinement.
  - Roles, in order of redundant priority (only ever one tank; as many healers as possible):
    - As many HEALERS as possible
    - As many TERTIARIES as possible
    - At least one PRIMARY
    - At least one SECONDARY
    - At least one TANK
- If someone can't fulfil any anni role they get FILL slots. At most 20–40% (organiser discretion) of a party can be fill before it's unlikely to succeed. Fill slots are not guaranteed.
- Always-relevant user statuses:
  - Membership: `MEMBER` > `WAITLIST` > `HONOURARY` > `COMMUNITY` > `ALLY` > `OTHER`
  - Capability priority.
  - Presence priority (`Here 1hr Early` = `Hard RSVP` > `Soft RSVP`)
  - A user can be: `DISAPPEARED`, `OFFLINE (HARD RSVP)`, `OFFLINE (SOFT RSVP)`, `ONLINE (WRONG WORLD)`, `ONLINE (NOT IN PARTY)`, `ONLINE (IN PARTY)`.

### Key Considerations

- The system will change; layout/structure/organisation must be well thought out, modular, and conducive to easy expansion. Minimise cognitive complexity. Includes DB migration capability later.
- Tasteful comments throughout so anyone can acclimate quickly.
- Some users AND staff are colourblind. Every interface MUST have a usable colourblind variant.
- Wynncraft users can disable their Wynn API: never show online, last seen = unix epoch. Guess status via changing server states (see dazebot purgelist).
- Parties can't easily be determined; rely on vetsmod hooks.
- To determine who is online, always use the same source as vetsmod's `/wv list` (far more accurate than the server API).
- Name desync: use the `legacyName` field on WAPI guild endpoints; cache of such users in temporary-server.
- Mimic the css of `…/website/public/returns/56/style.css`.
- The dashboards.pdf concept art is general direction only; better/more intuitive layouts may diverge. The Task section is what matters most.
- This application has its OWN token: WAPI ratelimits are not shared with the rest of vets-deploy.

### Environment (artefacts)

- Files in `.claude/ephemeral` are user-created session artefacts, gitignored, at risk of deletion.
- Anything worth keeping (memories/docs) should be promoted to `.claude/some.md` and linked in CLAUDE.md.

## The Task

### Application One: User-Facing

**General Dashboard → Login Screen:** ask for IGN + optional password. If a password is entered it is thereafter required for that username; if not, proceed directly. Minimal friction; staff tools needed to reset passwords.

**General Dashboard → Overview Screen:** generic info for all users (no personal assignments): time until anni, party status, etc.

**User Dashboard (logged in) → General Module** (any anni, not just current):
- *Registration Status:* membership type (Member/Community/Ally/Other) + eligibility (Core/Fill). Member = guild `Returners`; Community = guildless; Ally = guild whose tag is in the configured `ALLY_GUILD_TAGS` list (exact match — tags are case-sensitive); Other = any other guild. Bottom bar = attendance likelihood from the attendance priority table.
  - *Preferred region(s):* a user-set multi-select of **MaxMind GeoIP2 continent codes** (AF/AN/AS/EU/NA/OC/SA — all seven in the vocabulary, verbatim). Each renders as a globe glyph + code + full-name title (🌍 EU/AF, 🌎 NA/SA, 🌏 AS/OC, 🇦🇶 AN — glyph/colour is never the only signal). Pick zero or more; empty = "Any region" (no preference). The picker only **offers the enabled set** — the continents Wynn currently runs proxies for (`ENABLED_REGIONS`, default `AS,EU,NA`) — because offering regions Wynn can't host just confuses users; the capability to widen it (as Wynn adds proxies) is config-only, no redeploy. Codes outside the enabled set stay valid and still display if already stored — they just aren't offered or saved. On the user's own dashboard the bubbles sit on the Membership line with an "Edit Regions." button (no caption); editing is a **popup modal** (same pattern as the add-capability modal) so opening the picker never reflows the page. Also shown on their staff-board person card (organisers use it when balancing parties across play regions). Stored as a CSV of codes on `AnniPlayer.preferred_regions`.
- *Role Capacity:* up to five rows (primary/secondary/tertiary/healer/tank). Each row: weapons indicated; a High/Moderate/Low confidence/preference slider; a High/Moderate/Low build-quality slider; number of times assigned that role in a successful party. Editable. A button to add a new capability (with role guidance quoted/linked from the docs). Fill users get a red warning bar.

**User Dashboard → Specific Module** (blank if anni timestamp is in the past; else current anni; prominent countdown at top):
- *RSVP Status:* attendance type (weak vs strong RSVP vs 60-min-early vs late) + how we view their status (`offline (disappeared)`, `offline (strong rsvp)`, `offline (weak rsvp)`, `online (different world)`, `online (party world)`, `online (in-party)`). Bottom bar follows the escalation timing rules.
- *Tentative Information:* party number, leader, likely world, party status, assigned role. Bottom bar warns by party stage (stage 1 likely to change … stage 5 finalised, join now).

### Application Two: Discord-Facing

`/rsvp <revoke/hard/soft/status>` in dazebot or a purpose-built bot. Provides basic rsvp/anni insights + a link to the user's dashboard at https://anni.wynnvets.org/some-path.

### Application Three: Staff-Facing

**General Staff Dashboard:** staff-password login (admins can change it). Annihilation status section: when anni, who claimed organisation, staff online, party-formation status.

**Organizer Dashboard:** organise the event. After the timestamp, a 2h grace period to record per-party results (`LOSS`/`LAG`/`WIN`), then a board wipe. Parties left unset at wipe time default to `WIN` (staff had the whole grace window and didn't record a `LOSS`/`LAG`, so credited members still get their `success_count` bump).
- *Information Module:* live countdown + today's-organiser selector.
- *Legend Module:* explains border colours (presence status), background colours (assigned role) and the RSVP icons + a colourblind switch.
- *Attendance Bucket Modules:* one draggable object per potentially-eligible person (rsvp'd / 1hr-early / joined later), same source as `/wv list` + the bot `/rsvp`. Object shows username, legacy name, skin avatar, role-capability pills (weapon/confidence/refinement/success count), **preferred region(s)** (the user's MaxMind GeoIP2 continent codes, or "Any region" if unset), status border (presence only), an **RSVP ticket** left of the avatar, the same height as it (walk-in / hard / soft / retracted — the RSVP is its own channel, never folded into the border), assigned-role background (gray if unassigned), membership eligibility. **At most one instance of each person on the page.** People start unassigned and are moved by staff. Staff can also **manually add any user by IGN** to the Unassigned bucket (e.g. a walk-in who never RSVP'd and isn't in `/wv list`); the IGN is resolved to the canonical UUID and the one-instance-per-person rule still applies (re-adding someone already on the board is a no-op).
- *General Buckets (right):* Unassigned Attendees (unlimited; auto-populated from RSVP or 1hr-early, plus any user a staffer manually adds by IGN; sub-buckets in order: main, **Soft RSVP** — every soft RSVP, online or not — then walk-ins); Confirmed Nonattendance (unlimited; blanks their active-anni dashboard section); Willing to Sit Out (unlimited). Each **party** carries a narrower **Offline soft RSVPs** sub-bucket, shown only when occupied, holding the soft RSVPs who haven't turned up; they are auto-promoted back into the party proper the moment presence says they are online. Both soft lanes are *derived* from live state, not placements staff set — see `domain_rules.md`. There is no late sub-bucket: lateness is a per-card hourglass on a threshold that slides with the RSVP (T-15 hard / T-35 soft / T-50 walk-in; never for a retraction). Retracting an RSVP moves the card to Sitting out from wherever it sits, a party included.
- *Party Buckets (left):* Party N (cap 10). Unlimited parties; each has a host + world + stage (1–5). Assignments show on user dashboards.

**Role Dashboard:** everyone's listed capabilities; editable.

### Application Four: Vetsmod Additions

Eventually (as soon as practical): a `/anni` command showing key rsvp status, tasteful info, and perhaps player glow in role colours.

### Footnotes (key data sources)

- Eligibility: the attendance priority table — Capability (Fill vs Non-Fill), Membership, Notice (1hr Early, Hard RSVP, Soft RSVP). https://www.wynnvets.org/docs/guild/anni/#attending
- Staff list: temporary-server `staff_poller.py`; online subset served by the vets api (`/v1/outbound/staff`).
- Weapons: WAPI items endpoint (`/v3/item/search/{query}`).
- Anni timestamp: `api.wynnvets.org/v1/outbound/stamp` (plain text; past = next anni not yet announced).
- Role colours: red (primary), yellow (secondary), green (healer), cyan (fill), blue (tank), magenta (tertiary); grey = unassigned. One shared palette (`constants.STYLES`).
- Status colours: the border is a presence ramp, most→least "in position", and each step re-uses the hue of the ROLE it evokes as a mnemonic — online in party = FILL aqua `#00e1ff`, online correct world = HEALER green `#15ff00`, online wrong world = SECONDARY sun-gold `#fffb00`, offline = PRIMARY red `#ff0000`; grey = unknown. Safe to share with roles because they never share a channel (background/swatch vs border). It carries **no RSVP information**, and no history either: "was here and left" is an avatar stamp (a white `?`), which outranks the late-arrival hourglass when both apply and clears by itself when they log back on.
- RSVP ticket: its own channel on the person card, avatar-height, left of the face. Every state is a ticket (drawn to the `fa-slab-duo fa-regular fa-ticket` reference — ~1.25:1, two perforation bites per end): walk-in/no RSVP = yellow-green with a dashed outline and an exclamation, hard = green-blue with a tick, soft = red-purple with a clock, retracted = red-orange and torn in half. The hue's dark shade fills the ticket body (never a plate behind it) and the linework is its light tint, with a black hairline inside the line and a white one outside. Retracted outranks whatever notice was stored before it.
- Win credit: only players placed in a **party** earn `success_count` at the grace-wipe. Unassigned, Volunteering and Sitting-out earn nothing, assigned role or not.
