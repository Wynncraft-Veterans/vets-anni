# Colourblind variant (mandatory, every interface)

The spec makes this a hard requirement: some users *and staff* are colourblind,
and the dashboards are colour-dense.

## Mechanism
- A per-user `cb` cookie toggled by `GET /toggle-cb?next=…`, linked from a
  control present in the navbar of **every** page (and the organizer Legend
  module). No reload of state needed — it sets/clears the cookie and bounces
  back; `<body class="cb">` is added server-side.
- `static/css/anni.css` defines ONE canonical palette as `--c-*` custom
  properties (mirroring `constants.STYLES`); `--role-*`, `--st-*` and
  `--rsvp-*` are aliases onto it. The three families each pick their own
  entries — a role and a status sharing a hue is a coincidence, not a
  contract (the old one-to-one role↔status pairing is gone).
  `static/css/colourblind.css` swaps **only the `--c-*` base hues** under
  `body.cb` to the canonical **Okabe-Ito** CVD-safe set, so every alias
  follows in one step (swap is instant — class scope). `app/constants.py`
  (`STYLES`) stays the single source of truth for server-rendered
  colours/glyphs/labels.
- Colourblind mode is **purely a per-user `cb` cookie — there is no global,
  event, or admin default**. The world default is *always* full colour; a
  colourblind user who flips the toggle changes only *their own* view (their
  cookie), never anyone else's. `AppConfig` holds **no** colourblind key.
- **cb is a bundle of five features, each switchable** (2026-09-28):
  `lamps` (status traffic light instead of the border), `palette`
  (Okabe-Ito — *every* colour: roles, status borders, RSVP tickets),
  `codes` (text capability pips + their legend keys), `textures` (role
  card textures) and `nameplate` (a semi-transparent dark grey plate behind
  the name, the capability bar's height; with `codes` on too the two join
  into one strip — plate translucent, bar solid black). A static test
  checks every feature has CSS scoped under its class (no dead switches). `deps.CB_FEATURES` lists them; `deps.cb_features` says
  which are live — all False with cb off, all True by default with cb on,
  each with a one-year opt-out cookie `cbf_<name>=0` (independent of the
  login session, untouched by logout). base.html adds a `cbf-<name>` body
  class per live feature and colourblind.css scopes each feature's rules
  under `body.cb.cbf-<name>`, so a switched-off feature just falls back to
  the regular-mode styling underneath; the board legend reads the same
  flags (`cbf.codes`, `cbf.lamps`) to pick its keys.
- **The Accessibility menu** is where they are switched: under cb, the
  board's Configs box gains an "Accessibility Menu" switch (`cfg_a11y`,
  default on), which shows a full-width bar under the legend + Configs with
  one switch per feature. It sits inside `.legend-wrap`, so "Pin to top"
  pins it too. All switches go through `/toggle-label` (`which=a11y`,
  `which=cbf_<name>`).

## Colour is never the only signal
Every role/status/RSVP chip emitted by the shared macros
(`templates/macros/*`) carries, in addition to colour:
- a short **glyph** on role chips (`RoleStyle.glyph`, e.g. `PRIM`) — or, for
  the RSVP badge, an icon **shape** (`RsvpStyle.icon`),
- an accessible **label** (`aria-label`, e.g. "A RSVP'd user not here yet."),
- for statuses, under cb, a **traffic light** that *replaces* the status
  border: three lamps down the card's left edge (`status_lamps` in
  `macros/chips.html`, lit pattern from `StatusStyle.lamps`, top→bottom,
  `1` = lit). Lit = white, dark = black, each lamp with a light grey
  hairline so a dark lamp still shows on a black FILL card. The lit lamp
  climbs with presence — bottom = online elsewhere (`001`), middle = on
  the party's world (`010`), top = in the party (`100`); none lit =
  offline (`000`); top + bottom = unknown (`101`), a shape no single step
  makes. Position is the whole channel, so it needs no hue at all.
  Always in the DOM; anni.css hides it outside cb, where the coloured
  border carries status. It replaced a **border pattern** under cb
  (`double`→`solid`→`dash`→`dash-dot`, `dash-dot-dot` = unknown, drawn as
  a ring outside the card), which was too confusing to read at a glance
  (user report, 2026-09-28); `data-pattern` and its CSS are gone, and a
  static test guards against a `[data-pattern=` rule creeping back.
- for the RSVP badge, its ticket. All four states are tickets (`templates/macros/icons.html`), so the
  non-colour channel is three things at once: what is printed on the ticket
  (exclamation = walk-in, tick = hard, clock = soft, nothing = revoked),
  whether the outline is solid or dashed, and whether the silhouette is whole
  or torn. That is a finer distinction than four unrelated shapes would be,
  which is the cost of the family reading as one set; the dashed outline and
  the tear are carrying real weight for it, since outline style and
  silhouette both survive greyscale even when the interior mark is small.
  `test_colourblind.py` asserts every state has a distinct shape *and* that
  the macro actually draws it — a missing branch would render an empty
  `<svg>`, which fails silently rather than loudly. The icon takes its hue
  from the wrapper via `currentColor`; the only literals it may contain are
  the two achromatic keylines (black inside the line, white outside), and a
  test enforces that too.
- for assigned roles, a **card-background texture** keyed off the `data-role`
  attribute on `.person` (already in the DOM at all times). Six visually
  distinct shape rhythms — PRIMARY `/` 45° stripes, SECONDARY `\` 135°
  stripes, TERTIARY vertical stripes, HEALER crosshatch, TANK dot grid, FILL
  horizontal stripes — drawn as a white `background-image` overlay on top of
  the inline `--role-*-dark` background-color. CB-only (under `body.cb`);
  the rhythm carries the load when colour does not (achromatopsia, where
  Okabe-Ito hues collapse to similar luminance). **Bold, not faint**: ~22%
  white lines, 3px every 11px (dots/crosshatch matched), roughly 2:1
  luminance contrast against each card in greyscale. The first version
  (~7%, 2px/14px) was near-invisible — outright invisible on FILL, whose cb
  background is black (user report, 2026-09-28). To keep white text legible
  across the brighter stripes, text sitting directly on the card (the name,
  the legend chip label) gets a tight dark `text-shadow` halo; everything
  else on a card brings its own solid background. The cb legend's role chips
  stand 2.2rem tall (as do the status pills and the reliability key beside
  them) so the key shows a few repeats of each rhythm — at the default chip
  height the dots and crosshatch showed barely two — and the reliability
  box has black above its overline rather than the white card. Verified
  by rendering one card per role and converting the screenshot to
  greyscale. Unassigned cards
  stay flat — "no texture" maps to "no role". The **board legend** chips (`body.cb .legend .role-chip[data-role=…]`)
  share the same rule so the legend is the key — without it the textures on
  cards have no glossary. The smaller cross-dashboard role chips emitted by
  the `role_bg` macro stay clean (chip-size texture reads as noise at that
  scale; the legend chips are larger and exist precisely as a teach-aid). A
  static test (`test_colourblind.py`) asserts a rule exists for every
  assignable role.
- for **capability pips**, **text** replaces the pips outright under
  `body.cb`: the row becomes ONE black box of white two-letter role codes
  (`PR`/`SU`/`HD`/`HE`/`TA`/`FI` — `RoleStyle.code` in `constants.py`, two
  letters of each glyph), widely gapped, with reliability drawn as **lines
  on the code** — low = plain, moderate = underline, high = underline +
  overline. A line is there or it isn't; nothing has to be judged. Every
  code is the same type, chosen only to be read: **Verdana Bold** (DejaVu
  Sans on Linux), the crispest at 1x of the faces compared.
  Tried and dropped along the way (2026-09-28): type weight as the channel
  — regular-vs-bold was too close to call at this size, and even a vendored
  JetBrains Mono's Light-vs-ExtraBold left the light codes hard to read; a
  text-stroke "heavy" smeared at 1x; a box per code read as competing
  buttons; a light outline on the box read as more underlines. Mono was
  only there for exact widths, so it went with the weight channel.
  The card box is a fixed width — a full five-code row, measured in em
  (11.9em), as a `min-width` so a wider fallback face grows it rather than
  spilling the fifth code. The on-card pip is a `<button>`, which anni.css
  sets to weight 500; the cb reset gives it `font: inherit` or Verdana drops
  to regular. The bar is on **every** card under cb — the macro always emits
  the row, flagged `data-empty` when there are no stated capabilities (or
  the card is an unregistered placeholder); default mode hides an empty
  row, cb draws it as a blank bar of the same height. The default pip
  stacks role hue + reliability shape (triangle/square/circle) + a letter
  squeezed inside, all at ~.6rem; with hue unreliable, that left the shapes
  and a one-letter glyph doing the work at a size too small to read.
  Black/white is maximum contrast under every CVD type and in greyscale.
  Same DOM in both modes — the code is always in `.cap-letter`, CSS just
  shows it and strips the shape styling. Static tests pin the three
  line levels and that every role's code is distinct.
  The board legend is `{% if cb %}`-switched to match (like the status
  row's glyph), in two places: the reliability key is one sample box
  holding each level's own word with its lines, so the key is its own
  example; and each role chip swaps its colour swatch (`PRIM`…) for its
  code in the same black box (`.role-code`), so the role row decodes the
  pips. Those key codes are plain, same as "low" — unavoidable, and the
  role row sits apart from the reliability key. `FI` only ever appears in
  the legend: FILL is assign-only and never a capability.

**Okabe-Ito under `body.cb`:** card *backgrounds* are the `--role-*-dark`
aliases, remapped under `body.cb` to **darkened Okabe-Ito** shades (same hue
family, dark enough for legible plain-white text, ≥~4.7:1). There is no
status border under cb any more (the traffic light carries status), so the
old "verbatim border hue" rules went with it; the no-`groove`/`ridge`/
`inset`/`outset` guard stays in the test, since those 3-D styles shade a
colour rather than draw it.

**Traffic light and code bar are CB-only channels**: with `cb` off the
status border is a single SOLID coloured outline and the capability pips
are coloured shapes (colour is reliable for non-CVD users). Both cb
channels are **always emitted** in the DOM regardless of `cb` (the macros
never stop) and CB merely activates the CSS. The person root's `aria-label`
(role+status) is always emitted too, so screen-reader users get the full
signal in both modes.

The board/dashboards remain fully usable in greyscale or any CVD type (with
`cb` on: traffic light + capability code bar + role texture + aria-label;
the colourblind variant is never colour-only at the DOM level).

### Card layout: three lines at most
Line 1 is the name + capability bar (the bar never wraps; a long name
ellipsises, full name in its `title`, and only once name + bar overflow the
whole card width). Lines 2–3 are the tags, clamped to two rows
(`.person-tags` max-height, overflow hidden), with the role dropdown pinned
bottom-right beside them (`.person-bottom`). The opt-in Dropdown-move select
sits under the ticket + avatar instead (`.person-id.has-move`, both modes):
the pair moves to the top and shrinks a touch (2.2rem/32px → 1.85rem) so
the select fits beneath them inside the card's existing height — measured
identical with the dropdown on and off. It used to stack under the role
select, which made every card a line taller and squeezed the tag pills.
The closed control is set in Tahoma Bold (hand-hinted for small screen
sizes; crisper at 1x than system UI, Verdana or Atkinson Hyperlegible —
Bell Centennial, the brief, is commercial and not web-licensed) with a
"Move to…" placeholder that fits; the list it opens goes back to normal
page type (.85rem regular), since only the control has to be tiny.

**Removed (2026-09-28): the "Role/Status info" switch** (`lbl_tags` cookie,
`/toggle-label?which=tags`, `deps.labels_visible`, the `hide-rolelabel`/
`hide-statuslabel` body classes, and the card's role + status text tags) —
from both modes. It was rarely used, it undercut the card's visual channels
by restating them as pills, and it was the prime cause of card overflow.
`/toggle-label` still serves `pin` and `dropdown_assign`; a stale `tags`
link or cookie is a harmless no-op.

## Role-chip rendering (legibility)
In **normal** mode a role chip's **body** uses the `--role-*-dark` shade
(white text is legible on it for *every* role; the bright base hues like
green/yellow are not); the small **glyph swatch** keeps the *raw* `--role-*`
hue. Under **`body.cb`** the chip body alias `--role-*-dark` is *also*
remapped — to **darkened** Okabe-Ito shades (same hue family as the bright
swatch, dark enough to keep plain white text legible, ≥~4.7:1) — otherwise
the user dashboard, whose only palette element is the role chip, looked
identical in cb. Role identity survives via hue + glyph + label in both
modes; nothing is colour-only. The **status** border / traffic light is
a **staff-board** device (Phase 2): the Phase-1 user dashboard deliberately
shows presence as plain words + the escalating warning bar instead (no
staff-style indicator for end users).

## Two axes on one card
The person card carries **two independent colour channels**, and the CB rules
above apply to each separately:

| Channel | Where | Vocabulary | Non-colour signal |
|---|---|---|---|
| Presence | border around the card (cb: traffic light at its left edge) | `PresenceStatus` (5) | cb lamps + `aria-label` |
| History | one stamp centred on the avatar | gone (`?`) else late (hourglass) | distinct glyph + `title` + `aria-label` |
| RSVP | ticket left of the face | `RsvpState` (4) | printed mark + outline style + silhouette + `title` + `aria-label` |

They were one channel until the status border was reduced to presence alone;
keeping them separate is what lets an organiser read "online, but only ever
soft-RSVP'd" at a glance. The board legend renders a key for each — the RSVP
row shows its icon in **both** modes (unlike the status chips, which show
their traffic light only under cb and let colour carry it otherwise),
because the shape is the badge's only non-colour channel.

**Legend layout (both modes):** three rows — role keys / reliability +
statuses / RSVPs — unless any of them would wrap, in which case board.js
(`fitLegend`) adds `.legend-flow` and the three groups run together as one
wrapping row, split by `.legend-sep-flow` rules that only show in that
mode. The legend is pinned over the board, so every line it wraps to hides
a line of board; flowing is the cheaper failure. CSS cannot ask "did this
row wrap?", so it is measured (a one-line flex row is exactly as tall as
its tallest item) from the structured layout every time — on load, on
resize, and after every `#board` swap, which brings the legend back
structured. Without JS it simply stays structured.

Roles and statuses now share the neon palette *deliberately* — each status
takes the hue of the role it evokes, as a mnemonic (see `domain_rules.md`).
That is safe because they never share a channel: a role is a background or a
glyph swatch, a status is the border. RSVP tickets keep the mid-tone set,
being objects on the card rather than colourings of it. Under CB everything
collides more, since there are only nine CVD-safe hues for every assignment.
The invariant that actually matters, and is tested in both palettes, is that
the five status hues never collide with each other.
