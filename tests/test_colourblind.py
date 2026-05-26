"""Colourblind variant is a HARD spec requirement — assert it structurally.

Three layers: (1) every chip carries non-colour signal in ``constants``;
(2) the domain chip-builders surface glyph/label/pattern; (3) the CSS truly
swaps all seven base hues under ``body.cb``. If any regresses, colour becomes
load-bearing and the spec is violated.
"""

from __future__ import annotations

from pathlib import Path

from app.constants import (
    ROLE_STYLES,
    STATUS_STYLES,
    STYLES,
    PaletteColor,
    PresenceStatus,
    Role,
)
from app.domain.colourblind import role_chip, status_chip

_STATIC = Path(__file__).resolve().parents[1] / "static" / "css"


def test_every_role_has_a_glyph_and_label():
    for role in Role:
        s = ROLE_STYLES[role]
        assert s.glyph and s.label, role


def test_every_status_has_glyph_label_and_border_pattern():
    for status in PresenceStatus:
        s = STATUS_STYLES[status]
        assert s.glyph and s.label and s.pattern, status
    # One uniform family, most→least "present" PARTY→GONE, all distinct.
    seq = [
        STATUS_STYLES[s].pattern for s in (
            PresenceStatus.ONLINE_PARTY, PresenceStatus.ONLINE_WORLD,
            PresenceStatus.ONLINE_ELSEWHERE, PresenceStatus.OFFLINE_HARD,
            PresenceStatus.OFFLINE_SOFT, PresenceStatus.OFFLINE_GONE)
    ]
    assert seq == ["double", "solid", "dash", "dash-dash-dot",
                   "dash-dot", "dot"]
    assert STATUS_STYLES[PresenceStatus.UNKNOWN].pattern == "dash-dot-dot"
    assert len(set(seq + ["dash-dot-dot"])) == 7  # every pattern distinct


def test_palette_actually_changes_under_cb():
    assert set(STYLES) == set(PaletteColor)
    for colour, style in STYLES.items():
        assert style.cb != style.color, f"{colour} CB hue must differ"


def test_domain_chip_builders_emit_non_colour_signal():
    tank = role_chip(Role.TANK)
    assert tank["css_var"] == "--role-tank" and tank["glyph"] and tank["label"]
    unassigned = role_chip(None)
    assert unassigned["css_var"] == "--role-unassigned"
    unknown = status_chip(PresenceStatus.UNKNOWN)
    assert unknown["pattern"] == "dash-dot-dot"
    assert unknown["css_var"] == "--st-unknown"


def test_css_defines_base_hues_and_swaps_all_seven_under_body_cb():
    anni = (_STATIC / "anni.css").read_text(encoding="utf-8")
    cbc = (_STATIC / "colourblind.css").read_text(encoding="utf-8")
    hues = ["--c-red", "--c-yellow", "--c-green", "--c-blue", "--c-cyan",
            "--c-magenta", "--c-grey"]
    for h in hues:
        assert h in anni, f"{h} base hue missing from anni.css"
    assert "body.cb" in cbc
    cb_block = cbc[cbc.index("body.cb"):]
    for h in hues:
        assert h in cb_block, f"{h} not swapped under body.cb"
    # Every status pattern has a body.cb rule (the channel exists from start).
    for pat in ("solid", "double", "dash", "dot", "dash-dot",
                "dash-dash-dot", "dash-dot-dot"):
        assert f'body.cb .status-border[data-pattern="{pat}"]' in cbc

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
        assert f'body.cb .person[data-role="{r}"]' in cbc, (
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
    import re

    anni = (_STATIC / "anni.css").read_text(encoding="utf-8")
    cbc = (_STATIC / "colourblind.css").read_text(encoding="utf-8")
    root = anni[anni.index(":root"):anni.index("}", anni.index(":root"))]
    start = cbc.index("body.cb {")
    cb_block = cbc[start:cbc.index("}", start)]  # the body.cb declarations only

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
