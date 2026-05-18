"""Preferred play region(s) — pure helpers over the MaxMind GeoIP2
continent-code vocabulary in ``app.constants``.

``AnniPlayer.preferred_regions`` is a CSV of :class:`ContinentCode` values.
Everything that reads or writes it goes through here so parsing,
de-duplication, canonical ordering and the *enabled* filter live in exactly
one place (and stay unit-testable without a DB, FastAPI, or discord — see
``app.domain``). "Enabled" = the continents Wynn currently runs proxies for
(``app.settings.enabled_regions``); regions outside it stay valid + still
display if already stored, they're just not offered/saved.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.constants import (
    CONTINENT_GLYPH,
    CONTINENT_LABEL,
    CONTINENT_ORDER,
    ContinentCode,
)


def parse(raw: str | None) -> list[ContinentCode]:
    """CSV string -> valid, de-duped codes in canonical order.

    Tolerant by design (it parses user/stored/env input): whitespace and case
    are normalised and unknown tokens are dropped rather than raising, so a
    stale, hand-edited or now-disabled value can never break the dashboard.
    """
    if not raw:
        return []
    chosen: set[ContinentCode] = set()
    for part in raw.split(","):
        token = part.strip().upper()
        if not token:
            continue
        try:
            chosen.add(ContinentCode(token))
        except ValueError:
            continue
    return [c for c in CONTINENT_ORDER if c in chosen]


def coerce(values: Iterable[str] | None) -> list[ContinentCode]:
    """An iterable of code strings (e.g. ``settings.enabled_regions``) ->
    valid codes in canonical order. Thin wrapper over :func:`parse`."""
    return parse(",".join(values) if values else "")


def to_csv(codes: Iterable[ContinentCode]) -> str:
    """De-duped codes -> the canonical CSV stored on the player ("" if none)."""
    chosen = set(codes)
    return ",".join(c.value for c in CONTINENT_ORDER if c in chosen)


def restrict(
    codes: Iterable[ContinentCode], enabled: Iterable[ContinentCode]
) -> list[ContinentCode]:
    """Keep only codes Wynn currently offers (used when *saving* a selection
    so a crafted POST can't store a region Wynn can't host)."""
    allowed = set(enabled)
    return [c for c in CONTINENT_ORDER if c in set(codes) and c in allowed]


def choices(
    enabled: Iterable[ContinentCode] | None = None,
) -> list[tuple[ContinentCode, str]]:
    """``(code, full-name)`` pairs in display order. With ``enabled`` given,
    only those continents are returned (the picker's offer set); ``None`` =
    the full MaxMind vocabulary."""
    allowed = None if enabled is None else set(enabled)
    return [
        (c, CONTINENT_LABEL[c])
        for c in CONTINENT_ORDER
        if allowed is None or c in allowed
    ]


def labelled(raw: str | None) -> list[dict[str, str]]:
    """A player's regions as ``[{"code","label","glyph"}]`` for the read
    pills/cards — every stored region renders, even one since disabled."""
    return [
        {
            "code": c.value,
            "label": CONTINENT_LABEL[c],
            "glyph": CONTINENT_GLYPH[c],
        }
        for c in parse(raw)
    ]


def options(
    selected: Iterable[ContinentCode], enabled: Iterable[ContinentCode]
) -> list[dict[str, object]]:
    """The picker rows: every *enabled* continent as
    ``{"code","label","glyph","selected"}`` in canonical order."""
    chosen = set(selected)
    return [
        {
            "code": c.value,
            "label": label,
            "glyph": CONTINENT_GLYPH[c],
            "selected": c in chosen,
        }
        for c, label in choices(enabled)
    ]
