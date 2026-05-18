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
