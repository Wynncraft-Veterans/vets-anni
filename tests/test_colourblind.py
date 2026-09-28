"""Colourblind variant is a HARD spec requirement — assert it structurally.

Three layers: (1) every chip carries non-colour signal in ``constants``;
(2) the domain chip-builders surface glyph/label/lamps; (3) the CSS truly
swaps all seven base hues under ``body.cb``. If any regresses, colour becomes
load-bearing and the spec is violated.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.constants import (
    ROLE_STYLES,
    RSVP_STYLES,
    STATUS_STYLES,
    STYLES,
    PaletteColor,
    PresenceStatus,
    Role,
    RsvpState,
)
from app.domain.colourblind import role_chip, rsvp_chip, status_chip

_STATIC = Path(__file__).resolve().parents[1] / "static" / "css"
_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def test_every_role_has_a_glyph_and_label():
    for role in Role:
        s = ROLE_STYLES[role]
        assert s.glyph and s.label, role


def test_cb_pip_reliability_is_drawn_as_lines():
    """Under cb a pip is its role code, and reliability is the lines drawn
    on it — none / underline / underline + overline. Each level needs its
    own rule; a missing one would silently merge two levels."""
    cbc = (_STATIC / "colourblind.css").read_text(encoding="utf-8")

    def lines_for(level: str) -> str | None:
        m = re.search(
            rf'data-reliability="{level}"\]\s*\.cap-letter\s*\{{([^}}]*)\}}', cbc)
        if not m:
            return None
        d = re.search(r"text-decoration-line:\s*([^;]+);", m.group(1))
        return d.group(1).strip() if d else None

    assert lines_for("low") is None            # plain: no rule of its own
    assert lines_for("moderate") == "underline"
    assert lines_for("high") == "underline overline"


def test_every_cb_feature_switch_has_css_to_switch():
    """Each Accessibility-menu switch toggles a `cbf-<name>` body class, and
    only CSS scoped under that class makes the feature do anything. A switch
    with no scoped rule would be a dead toggle."""
    from app.web.deps import CB_FEATURES

    css = ((_STATIC / "colourblind.css").read_text(encoding="utf-8")
           + (_STATIC / "anni.css").read_text(encoding="utf-8"))
    for name in CB_FEATURES:
        assert f"body.cb.cbf-{name}" in css, f"no CSS behind the {name!r} switch"


def test_every_role_has_a_distinct_two_letter_code():
    """Under cb the capability pips ARE these codes (white text in a black
    box), so two roles sharing one would make them indistinguishable — the
    box is achromatic by design, leaving the text as the only channel."""
    codes = [ROLE_STYLES[role].code for role in Role]
    assert all(len(c) == 2 and c.isupper() for c in codes), codes
    assert len(set(codes)) == len(codes), f"roles share a code: {codes}"


def test_every_status_has_glyph_label_and_traffic_light():
    """Under cb, status is a three-lamp traffic light (top→bottom, 1 = lit)
    and nothing else — no border, no hue — so every status needs its own
    lamp pattern. The lit lamp climbs with presence; none lit is offline;
    top + bottom (a shape no single step makes) is unknown."""
    for status in PresenceStatus:
        s = STATUS_STYLES[status]
        assert s.glyph and s.label, status
        assert len(s.lamps) == 3 and set(s.lamps) <= {"0", "1"}, status
    lamps = {s: STATUS_STYLES[s].lamps for s in PresenceStatus}
    assert lamps == {
        PresenceStatus.ONLINE_PARTY: "100",
        PresenceStatus.ONLINE_WORLD: "010",
        PresenceStatus.ONLINE_ELSEWHERE: "001",
        PresenceStatus.OFFLINE: "000",
        PresenceStatus.UNKNOWN: "101",
    }
    assert len(set(lamps.values())) == len(PresenceStatus)
    # Glyphs are the other non-colour channel and must be distinct too.
    assert len({STATUS_STYLES[s].glyph for s in PresenceStatus}) == len(
        PresenceStatus)


def test_rsvp_is_a_separate_axis_with_its_own_non_colour_signal():
    """RSVP came OUT of the status border and became its own icon. The badge
    has no chip or outline box, so the icon SHAPE is its whole non-colour
    channel — every state needs a distinct one, and macros/icons.html must
    actually draw it (a missing branch renders an empty <svg>, which is
    invisible rather than loud)."""
    for state in RsvpState:
        s = RSVP_STYLES[state]
        assert s.icon and s.label, state
    icons = {RSVP_STYLES[s].icon for s in RsvpState}
    assert len(icons) == len(RsvpState), "two states share a shape"

    hard = rsvp_chip(RsvpState.HARD)
    assert hard["css_var"] == "--rsvp-hard" and hard["state"] == "hard"
    # Both channels of the hue: the ticket's strokes and the plate behind it.
    assert hard["css_var_dark"] == "--rsvp-hard-dark"
    assert hard["icon"] == RSVP_STYLES[RsvpState.HARD].icon

    macro = (_TEMPLATES / "macros" / "icons.html").read_text(encoding="utf-8")
    for icon in icons:
        assert f'name == "{icon}"' in macro, f"icons.html draws no {icon!r}"
    # The icon takes its hue from the wrapper and never names one itself —
    # that is what lets ONE file serve every state in both palettes. The only
    # literals allowed are the two achromatic keylines the rings are built
    # from (black inside the line, white outside it).
    assert "currentColor" in macro
    literals = set(re.findall(r"#[0-9a-fA-F]{3,8}", macro))
    assert literals <= {"#000", "#fff"}, f"icons.html hardcodes a hue: {literals}"


def test_palette_actually_changes_under_cb():
    assert set(STYLES) == set(PaletteColor)
    for colour, style in STYLES.items():
        assert style.cb != style.color, f"{colour} CB hue must differ"


def test_the_status_ramp_stays_mutually_distinct():
    """Role and status hues are allowed to collide (background vs border, and
    every chip carries a glyph anyway). What is NOT allowed is two *statuses*
    sharing a hue — the border is the same channel in the same place, so a
    collision there is a genuine ambiguity. Default palette only: under cb
    there is no status border to colour (the traffic light carries it)."""
    hues = [STATUS_STYLES[s].color for s in PresenceStatus]
    assert len(set(hues)) == len(hues), "status hues collide"


def test_domain_chip_builders_emit_non_colour_signal():
    tank = role_chip(Role.TANK)
    assert tank["css_var"] == "--role-tank" and tank["glyph"] and tank["label"]
    unassigned = role_chip(None)
    assert unassigned["css_var"] == "--role-unassigned"
    unknown = status_chip(PresenceStatus.UNKNOWN)
    assert unknown["lamps"] == "101"
    assert unknown["css_var"] == "--st-unknown"


def test_css_defines_base_hues_and_swaps_every_one_under_body_cb():
    anni = (_STATIC / "anni.css").read_text(encoding="utf-8")
    cbc = (_STATIC / "colourblind.css").read_text(encoding="utf-8")
    # Derived from the enum, not hand-listed: a new palette entry that nobody
    # remembered to add to the stylesheets should fail HERE, not in a browser.
    hues = [f"--c-{c.value}" for c in PaletteColor]
    for h in hues:
        assert h in anni, f"{h} base hue missing from anni.css"
    assert "body.cb" in cbc
    cb_block = cbc[cbc.index("body.cb"):]
    for h in hues:
        assert h in cb_block, f"{h} not swapped under body.cb"
    # Status under cb is the traffic light: the lamps are drawn (and lit)
    # there, and the retired border-pattern ring has left no rules behind.
    assert "body.cb.cbf-lamps .status-lamps" in cbc and "i.lit" in cbc
    assert not re.search(r"\[data-pattern=", cbc), "dead border-pattern rule"

    # Borders are VERBATIM Okabe-Ito under cb: the body.cb --c-* hex are
    # exactly STYLES[*].cb (single source of truth) ...
    for colour, style in STYLES.items():
        assert f"--c-{colour.value}" in cb_block
        assert style.cb in cb_block, f"{colour} Okabe-Ito hex missing under cb"
    # ... and NO 3-D border *style* is used anywhere (groove/ridge/inset/
    # outset lighten/darken the colour — that would break "verbatim"). Check
    # the actual declaration, not the word (it's fine in a comment).
    for bad in ("groove", "ridge", "inset", "outset"):
        assert f"border-style: {bad}" not in cbc, f"{bad} shades the colour"
        assert f"border-style:{bad}" not in cbc, f"{bad} shades the colour"

    # Card BACKGROUNDS are darkened-Okabe-Ito (white text legible): every
    # role-*-dark alias is remapped under body.cb.
    for r in ("primary", "secondary", "tertiary", "healer", "tank", "fill",
              "unassigned"):
        assert f"--role-{r}-dark:" in cb_block

    # Every ASSIGNABLE role has a CB-only card texture (a non-colour channel
    # for achromatopsia, since Okabe-Ito collapses in greyscale). Unassigned
    # stays flat — "no texture" maps to "no role".
    for r in ("primary", "secondary", "tertiary", "healer", "tank", "fill"):
        assert f'body.cb.cbf-textures .person[data-role="{r}"]' in cbc, (
            f"missing CB card texture for role={r}")


def _comment_fault(css: str) -> str | None:
    """None if every CSS comment is well-formed; else a description. Catches
    a stray ``*/`` (e.g. a glob asterisk-then-slash written inside a comment,
    which closes it early) or an unterminated comment — both silently corrupt
    the stylesheet and the CSS parser drops whole rule blocks."""
    i, n = 0, len(css)
    while i < n:
        op = css.find("/*", i)
        stray = css.find("*/", i, op if op != -1 else n)
        if stray != -1:
            ctx = css[max(0, stray - 40):stray + 2].replace("\n", " ")
            return f"stray '*/' (premature comment close) near: …{ctx}"
        if op == -1:
            return None
        cl = css.find("*/", op + 2)
        if cl == -1:
            return f"unterminated comment opened near: {css[op:op + 50]!r}"
        i = cl + 2
    return None


def test_css_comments_are_well_formed():
    """The exact bug that shipped: a comment containing a glob `--role-*` then
    `/--st-*` had an asterisk-slash that closed the comment early, corrupting
    colourblind.css so the whole body.cb variable block was discarded by the
    browser (CB mode showed the bright default palette). Guard both sheets."""
    for name in ("anni.css", "colourblind.css"):
        fault = _comment_fault((_STATIC / name).read_text(encoding="utf-8"))
        assert fault is None, f"{name}: {fault}"


def test_every_var_c_alias_is_re_declared_under_body_cb():
    """Regression: a `--x: var(--c-*)` alias declared in anni.css `:root` is
    resolved *on :root* (where --c-* is the DEFAULT palette — body.cb only
    swaps --c-* on <body>). So body.cb MUST re-declare every such alias or CB
    mode silently keeps the bright #ff0000 etc. (the exact bug that shipped:
    red borders/glyphs in CB). This catches it without a browser."""
    anni = (_STATIC / "anni.css").read_text(encoding="utf-8")
    cbc = (_STATIC / "colourblind.css").read_text(encoding="utf-8")
    root = anni[anni.index(":root"):anni.index("}", anni.index(":root"))]
    start = cbc.index("body.cb.cbf-palette {")
    cb_block = cbc[start:cbc.index("}", start)]  # the palette declarations only

    # Aliases anni.css :root defines purely as `var(--c-...)`.
    aliases = re.findall(r"(--[\w-]+)\s*:\s*var\(\s*(--c-[\w-]+)\s*\)\s*;",
                         root)
    assert aliases, "no `--x: var(--c-*)` aliases found — parser drifted?"
    missing = [
        a for a, _ in aliases
        if not re.search(rf"{re.escape(a)}\s*:", cb_block)
    ]
    assert not missing, (
        "these --c-* aliases are NOT re-declared under body.cb, so CB mode "
        f"keeps the default palette for them: {sorted(set(missing))}"
    )
