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
    # Two non-colour families so the border alone reads online vs offline.
    online = {STATUS_STYLES[s].pattern for s in (
        PresenceStatus.ONLINE_ELSEWHERE, PresenceStatus.ONLINE_WORLD,
        PresenceStatus.ONLINE_PARTY)}
    offline = {STATUS_STYLES[s].pattern for s in (
        PresenceStatus.OFFLINE_HARD, PresenceStatus.OFFLINE_SOFT,
        PresenceStatus.OFFLINE_GONE)}
    assert online == {"solid", "double", "triple"}
    assert offline == {"long-dash", "short-dash", "dotted"}
    assert STATUS_STYLES[PresenceStatus.UNKNOWN].pattern == "wavy"


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
    assert unknown["pattern"] == "wavy" and unknown["css_var"] == "--st-unknown"


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
    # The non-colour border patterns must exist as a channel from the start.
    for pat in ("solid", "double", "triple", "long-dash", "short-dash",
                "dotted", "wavy"):
        assert f'data-pattern="{pat}"' in cbc
