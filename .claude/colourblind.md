# Colourblind variant (mandatory, every interface)

The spec makes this a hard requirement: some users *and staff* are colourblind,
and the dashboards are colour-dense.

## Mechanism
- A per-user `cb` cookie toggled by `GET /toggle-cb?next=…`, linked from a
  control present in the navbar of **every** page (and the organizer Legend
  module). No reload of state needed — it sets/clears the cookie and bounces
  back; `<body class="cb">` is added server-side.
- `static/css/anni.css` defines ONE canonical palette as `--c-*` custom
  properties (mirroring `constants.STYLES`); `--role-*`/`--st-*` are aliases
  onto it, and a role shares its colour with its paired status.
  `static/css/colourblind.css` swaps **only the seven `--c-*` base hues**
  under `body.cb` to the canonical **Okabe-Ito** CVD-safe set, so every alias
  follows in one step (swap is instant — class scope). `app/constants.py`
  (`STYLES`) stays the single source of truth for server-rendered
  colours/glyphs/labels.
- Colourblind mode is **purely a per-user `cb` cookie — there is no global,
  event, or admin default**. The world default is *always* full colour; a
  colourblind user who flips the toggle changes only *their own* view (their
  cookie), never anyone else's. `AppConfig` holds **no** colourblind key.

## Colour is never the only signal
Every role/status chip emitted by the shared macros (`templates/macros/*`)
carries, in addition to colour:
- a short **glyph** (`RoleStyle.glyph` / `StatusStyle.glyph`, e.g. `PRIM`, `●`),
- an accessible **label** (`aria-label`, e.g. "A RSVP'd user not here yet."),
- for statuses, a **border pattern** (`StatusStyle.pattern`) via
  `data-pattern` → `colourblind.css`. ONE uniform-width family, most→least
  "present" (PARTY→GONE): `double`→`solid`→`dash`→`dash-dash-dot`→`dash-dot`
  →`dot`; `dash-dot-dot` = unknown.

**Verbatim Okabe-Ito under `body.cb`:** the border colour is *always* the
exact Okabe-Ito hue (`--stc`/`--st-*` → `--c-*`, == `STYLES[*].cb`). Only
`solid`/`double`/`dash`(dashed)/`dot`(dotted) are native border-styles (they
render `border-color` exactly); the composites (`dash-dot`, `dash-dash-dot`,
`dash-dot-dot`) are a `repeating-linear-gradient` **border-image fed by
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
still assert `data-pattern` present in both modes. glyph + aria-label are
likewise always emitted; under `body.cb` they are additionally forced
*visible* (the label-density toggles can hide the text tag only with cb off).

The board/dashboards remain fully usable in greyscale or any CVD type (with
`cb` on: glyph + label + border pattern; the colourblind variant is never
colour-only). This is verified by DOM inspection in the Phase 1/2 checks.

### Board label-density toggles (do not weaken the hard rule)
The organizer board's **Configs** box has per-user "Role Labels" / "Status
Labels" radio toggles (`lbl_roles`/`lbl_status` cookies, `GET /toggle-label`,
`deps.label_visible`; a sibling "Pin to top" config — `cfg_pin`, default on —
shares the same route/box but is unrelated to CB). They only hide the *text tag* on a person card — and
**only when `cb` is off**, where the card background (role) and the border
colour+pattern (status) already convey it for non-CVD users. Default is
**off** (hidden) with `cb` off, **forced on** with `cb` on: `label_visible`
returns `True` whenever `colourblind()` is true, and `colourblind.css` has a
`body.cb .person .role-tag/.status-tag { display:inline-flex !important }`
guard, so a stale pref cookie can never strip glyph+label under CB. The hard
rule ("colour is never the only signal in the colourblind variant") therefore
still holds — these toggles are a non-CB cosmetic density choice only. The
person root keeps its full `aria-label` (role+status) even when the tag is
visually hidden, so screen-reader users are unaffected.

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
