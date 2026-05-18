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
- *Role Capacity:* up to five rows (primary/secondary/tertiary/healer/tank). Each row: weapons indicated; a High/Moderate/Low confidence/preference slider; a High/Moderate/Low build-quality slider; number of times assigned that role in a successful party. Editable. A button to add a new capability (with role guidance quoted/linked from the docs). Fill users get a red warning bar.

**User Dashboard → Specific Module** (blank if anni timestamp is in the past; else current anni; prominent countdown at top):
- *RSVP Status:* attendance type (weak vs strong RSVP vs 60-min-early vs late) + how we view their status (`offline (disappeared)`, `offline (strong rsvp)`, `offline (weak rsvp)`, `online (different world)`, `online (party world)`, `online (in-party)`). Bottom bar follows the escalation timing rules.
- *Tentative Information:* party number, leader, likely world, party status, assigned role. Bottom bar warns by party stage (stage 1 likely to change … stage 5 finalised, join now).

### Application Two: Discord-Facing

`/rsvp <revoke/hard/soft/status>` in dazebot or a purpose-built bot. Provides basic rsvp/anni insights + a link to the user's dashboard at https://anni.wynnvets.org/some-path.

### Application Three: Staff-Facing

**General Staff Dashboard:** staff-password login (admins can change it). Annihilation status section: when anni, who claimed organisation, staff online, party-formation status.

**Organizer Dashboard:** organise the event. After the timestamp, a 2h grace period to record per-party results (`LOSS`/`LAG`/`WIN`), then a board wipe.
- *Information Module:* live countdown + today's-organiser selector.
- *Legend Module:* explains border colours (status) and background colours (assigned role) + a colourblind switch.
- *Attendance Bucket Modules:* one draggable object per potentially-eligible person (rsvp'd / 1hr-early / joined later), same source as `/wv list` + the bot `/rsvp`. Object shows username, legacy name, skin avatar, role-capability pills (weapon/confidence/refinement/success count), status border, assigned-role background (gray if unassigned), membership eligibility. **At most one instance of each person on the page.** People start unassigned and are moved by staff.
- *General Buckets (right):* Unassigned Attendees (unlimited; auto-populated from RSVP or 1hr-early; late joiners in a LATE sub-bucket); Confirmed Nonattendance (unlimited; blanks their active-anni dashboard section); Willing to Sit Out (unlimited).
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
- Status colours: each status border is the SAME shared colour as its paired role — offline disappearance = red (primary), offline weak rsvp = yellow (secondary), offline strong rsvp = green (healer), online wrong world = blue (tank), online correct world = cyan (fill), online in party = magenta (tertiary); grey = unknown.
