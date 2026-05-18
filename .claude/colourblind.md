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
  `data-pattern` → `colourblind.css`, in two families so the border alone
  reads online vs offline: ONLINE unbroken & escalating
  `solid`→`double`→`triple`; OFFLINE broken & degrading
  `long-dash`→`short-dash`→`dotted`; `wavy` = unknown.

The board/dashboards remain fully usable in greyscale or any CVD type. This
is verified by DOM inspection in the Phase 1/2 checks (the glyph + aria-label +
data-pattern must be present regardless of `cb`).

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
