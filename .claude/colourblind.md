# Colourblind variant (mandatory, every interface)

The spec makes this a hard requirement: some users *and staff* are colourblind,
and the dashboards are colour-dense.

## Mechanism
- A per-user `cb` cookie toggled by `GET /toggle-cb?next=…`, linked from a
  control present in the navbar of **every** page (and the organizer Legend
  module). No reload of state needed — it sets/clears the cookie and bounces
  back; `<body class="cb">` is added server-side.
- `static/css/anni.css` defines the default palette as CSS custom properties
  (`--role-*`, `--st-*`). `static/css/colourblind.css` overrides them under
  `body.cb` with an **Okabe-Ito-derived, CVD-safe** palette. Palette swap is
  instant (class scope) and authoritative values still live in
  `app/constants.py` for server-rendered glyphs/labels.
- Colourblind mode is **purely a per-user cookie**. There is intentionally no
  global/event default to manage — only a few users need the variant and they
  can flip their own cookie on any page in one click.

## Colour is never the only signal
Every role/status chip emitted by the shared macros (`templates/macros/*`)
carries, in addition to colour:
- a short **glyph** (`RoleStyle.glyph` / `StatusStyle.glyph`, e.g. `PRI`, `●`),
- an accessible **label** (`aria-label`, e.g. "Offline — hard RSVP (safe)"),
- for statuses, a **border pattern** (`StatusStyle.pattern`: solid/dashed/
  dotted/double/dash-dot/thick) via `data-pattern` → `colourblind.css`.

So the board/dashboards remain fully usable in greyscale or any CVD type. This
is verified by DOM inspection in the Phase 1/2 checks (the glyph + aria-label +
data-pattern must be present regardless of `cb`).
