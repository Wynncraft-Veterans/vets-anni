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

## Colour is never the only signal
Every role/status/RSVP chip emitted by the shared macros
(`templates/macros/*`) carries, in addition to colour:
- a short **glyph** (`RoleStyle.glyph` / `StatusStyle.glyph`, e.g. `PRIM`,
  `●`) — or, for the RSVP badge, an icon **shape** (`RsvpStyle.icon`),
- an accessible **label** (`aria-label`, e.g. "A RSVP'd user not here yet."),
- for statuses, a **border pattern** (`StatusStyle.pattern`) via
  `data-pattern` → `colourblind.css`. ONE uniform-width family, most→least
  "present": `double`→`solid`→`dash`→`dash-dot`; `dash-dot-dot` = unknown.
  (Two steps have retired as their distinctions moved to channels of their
  own: `dash-dash-dot` when the offline branch stopped being split
  hard-RSVP vs soft-RSVP, and `dot` when "was here and left" became an avatar
  stamp rather than a status.)
- for the RSVP badge, **no pattern** — there is no border to carry a rhythm.
  All four states are tickets (`templates/macros/icons.html`), so the
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
  distinct, faint shape rhythms — PRIMARY `/` 45° stripes, SECONDARY `\` 135°
  stripes, TERTIARY vertical stripes, HEALER crosshatch, TANK dot grid, FILL
  horizontal stripes — drawn as a low-alpha white `background-image` overlay
  on top of the inline `--role-*-dark` background-color. CB-only (under
  `body.cb`); the rhythm only carries load when colour does not (i.e. under
  achromatopsia, where Okabe-Ito hues collapse to similar luminance). The
  overlay is faint enough (≤ ~9% white) that the white chip text keeps its
  ≥ ~4.7:1 contrast. Unassigned cards stay flat — "no texture" maps to "no
  role". The **board legend** chips (`body.cb .legend .role-chip[data-role=…]`)
  share the same rule so the legend is the key — without it the textures on
  cards have no glossary. The smaller cross-dashboard role chips emitted by
  the `role_bg` macro stay clean (chip-size texture reads as noise at that
  scale; the legend chips are larger and exist precisely as a teach-aid). A
  static test (`test_colourblind.py`) asserts a rule exists for every
  assignable role.

**Verbatim Okabe-Ito under `body.cb`:** the border colour is *always* the
exact Okabe-Ito hue (`--stc`/`--st-*` → `--c-*`, == `STYLES[*].cb`). Only
`solid`/`double`/`dash`(dashed)/`dot`(dotted) are native border-styles (they
render `border-color` exactly); the composites (`dash-dot`, `dash-dot-dot`)
are a `repeating-linear-gradient` **border-image fed by
`var(--stc)`**, so the line is still the exact hue. No `groove`/`ridge`/
`inset`/`outset` anywhere — those 3-D-shade (lighten/darken) the colour and
would break "verbatim". Card *backgrounds* are the `--role-*-dark` aliases,
remapped under `body.cb` to **darkened Okabe-Ito** shades (same hue family,
dark enough for legible plain-white text, ≥~4.7:1).

The **border pattern is a CB-only channel**: with `cb` off the status border
is a single SOLID coloured outline (colour is reliable for non-CVD users —
the dash/dot/double rhythm was visual noise), and the pattern rules are
scoped under `body.cb`. The `data-pattern` attribute is **always emitted**
in the DOM regardless of `cb` (the macros never stop) so the channel "exists
from the start" and CB merely activates the CSS — the Phase 1/2 DOM checks
still assert `data-pattern` present in both modes. The person root's
`aria-label` (role+status) is always emitted too, so screen-reader users get
the full signal in both modes.

The board/dashboards remain fully usable in greyscale or any CVD type (with
`cb` on: Okabe-Ito hue + border pattern + capability dots + aria-label; the
colourblind variant is never colour-only at the DOM level). This is verified
by DOM inspection in the Phase 1/2 checks.

### Board label-density toggle
The organizer board's **Configs** box has a per-user "Role/Status info"
toggle (`lbl_tags` cookie, `GET /toggle-label?which=tags`,
`deps.labels_visible`; a sibling "Pin to top" config — `cfg_pin`, default on —
shares the same route/box but is unrelated to CB). It hides the *text tags*
on a person card (both the role tag and the status tag move together — they
are the same density choice). Default is **off in both modes**, opt-in: the
card background (role colour), border colour+pattern (status), and capability
dots already carry the signal, and the person root's `aria-label` is
unaffected. CB does not lock this toggle; a CB user opting out is making an
informed density choice.

## Role-chip rendering (legibility)
In **normal** mode a role chip's **body** uses the `--role-*-dark` shade
(white text is legible on it for *every* role; the bright base hues like
green/yellow are not); the small **glyph swatch** keeps the *raw* `--role-*`
hue. Under **`body.cb`** the chip body alias `--role-*-dark` is *also*
remapped — to **darkened** Okabe-Ito shades (same hue family as the bright
swatch, dark enough to keep plain white text legible, ≥~4.7:1) — otherwise
the user dashboard, whose only palette element is the role chip, looked
identical in cb. Role identity survives via hue + glyph + label in both
modes; nothing is colour-only. The **status** border/glyph/pattern is
a **staff-board** device (Phase 2): the Phase-1 user dashboard deliberately
shows presence as plain words + the escalating warning bar instead (no
staff-style indicator for end users).

## Two axes on one card
The person card carries **two independent colour channels**, and the CB rules
above apply to each separately:

| Channel | Where | Vocabulary | Non-colour signal |
|---|---|---|---|
| Presence | border around the whole card | `PresenceStatus` (5) | `data-pattern` + glyph + `aria-label` |
| History | one stamp centred on the avatar | gone (`?`) else late (hourglass) | distinct glyph + `title` + `aria-label` |
| RSVP | ticket left of the face | `RsvpState` (4) | printed mark + outline style + silhouette + `title` + `aria-label` |

They were one channel until the status border was reduced to presence alone;
keeping them separate is what lets an organiser read "online, but only ever
soft-RSVP'd" at a glance. The board legend renders a key for each — the RSVP
row shows its icon in **both** modes (unlike the status row, which drops the
glyph when `cb` is off and lets colour carry it), because the shape is the
badge's only non-colour channel.

Roles and statuses now share the neon palette *deliberately* — each status
takes the hue of the role it evokes, as a mnemonic (see `domain_rules.md`).
That is safe because they never share a channel: a role is a background or a
glyph swatch, a status is the border. RSVP tickets keep the mid-tone set,
being objects on the card rather than colourings of it. Under CB everything
collides more, since there are only nine CVD-safe hues for every assignment.
The invariant that actually matters, and is tested in both palettes, is that
the five status hues never collide with each other.
