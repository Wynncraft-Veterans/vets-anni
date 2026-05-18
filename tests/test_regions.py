"""Preferred-region(s) — pure parse/format/enabled rules + the edit flow.

The domain tests pin the deliberately-tolerant parser (a stale / hand-edited /
since-disabled ``preferred_regions`` CSV must never break a dashboard), the
canonical order-stable round-trip, and the *enabled set* (the picker only
offers continents Wynn proxies — default AS/EU/NA — but stored values outside
it still parse + display). The web tests cover the read pills, the enabled-
only picker, and the save (incl. the disabled-region clamp).
"""

from __future__ import annotations

import pytest_asyncio

from app.constants import CONTINENT_ORDER, ContinentCode
from app.db.models import AnniPlayer
from app.domain import regions
from app.web import deps

AS, EU, NA = ContinentCode.AS, ContinentCode.EU, ContinentCode.NA
AF, AN, OC, SA = (
    ContinentCode.AF,
    ContinentCode.AN,
    ContinentCode.OC,
    ContinentCode.SA,
)
ENABLED = [AS, EU, NA]  # the default offer set (Wynn's current proxies)


# --- pure domain -----------------------------------------------------------
def test_parse_empty_is_no_preference():
    assert regions.parse(None) == []
    assert regions.parse("") == []
    assert regions.parse(" , ,") == []


def test_parse_is_tolerant_and_order_stable():
    # lower/space/unknown-token tolerant; dupes collapse; canonical order out.
    assert regions.parse("na, eu , XX, eu") == [EU, NA]


def test_parse_accepts_all_seven_maxmind_codes_regardless_of_enabled():
    # parse is the tolerant *reader* — never filtered by the enabled set, so
    # a stored-but-now-disabled region still renders.
    every = ",".join(c.value for c in CONTINENT_ORDER)
    assert regions.parse(every) == list(CONTINENT_ORDER)


def test_coerce_from_iterable():
    assert regions.coerce(["na", "eu", "zz"]) == [EU, NA]
    assert regions.coerce([]) == [] and regions.coerce(None) == []


def test_to_csv_round_trips_canonically():
    csv = regions.to_csv([SA, AF, AF])
    assert csv == "AF,SA"  # de-duped + canonical order, not input order
    assert regions.parse(csv) == [AF, SA]
    assert regions.to_csv([]) == ""


def test_choices_filtered_by_enabled():
    assert [c for c, _ in regions.choices()] == list(CONTINENT_ORDER)  # all
    assert [c for c, _ in regions.choices(ENABLED)] == [AS, EU, NA]
    assert regions.choices([]) == []


def test_restrict_clamps_to_enabled():
    assert regions.restrict([SA, EU, AF, NA], ENABLED) == [EU, NA]
    assert regions.restrict([SA, AF], ENABLED) == []


def test_labelled_carries_glyph_and_ignores_enabled():
    # 🌍 EU/AF · 🌎 NA/SA · 🌏 AS/OC · 🇦🇶 AN
    assert regions.labelled("EU,NA,OC") == [
        {"code": "EU", "label": "Europe", "glyph": "🌍"},
        {"code": "NA", "label": "North America", "glyph": "🌎"},
        {"code": "OC", "label": "Oceania", "glyph": "🌏"},
    ]
    assert regions.labelled("AN") == [
        {"code": "AN", "label": "Antarctica", "glyph": "🇦🇶"}
    ]


def test_options_are_enabled_only_with_selection_flagged():
    opts = regions.options([NA], ENABLED)
    assert [o["code"] for o in opts] == ["AS", "EU", "NA"]  # no OC/SA/AF/AN
    assert [o["selected"] for o in opts] == [False, False, True]
    assert opts[2] == {
        "code": "NA", "label": "North America", "glyph": "🌎", "selected": True
    }


# --- web flow --------------------------------------------------------------
@pytest_asyncio.fixture
async def as_user(client, seeded):
    """``client`` signed in as seeded Wenweia (preferred_regions = 'EU,NA')."""
    player = seeded["players"]["Wenweia"]
    client.cookies.set(
        "anni_session",
        deps._serializer.dumps(
            {"kind": "user", "mc_uuid": player.mc_uuid, "name": player.mc_username}
        ),
    )
    return client


async def test_dashboard_shows_preferred_regions_with_glyphs(as_user):
    body = (await as_user.get("/me")).text
    # No caption — the button label is the only affordance text.
    assert ">Edit Regions.</button>" in body
    assert "Preferred region(s)" not in body  # caption removed; modal not loaded
    # Seeded EU,NA -> per-continent globe + code, full-name title (CB-safe).
    assert 'title="Europe"' in body and ">🌍 EU<" in body
    assert 'title="North America"' in body and ">🌎 NA<" in body
    # Edit opens the popup (loads into #modal-mount) — no inline expansion.
    assert 'hx-get="/me/regions/edit"' in body
    assert 'hx-target="#modal-mount"' in body
    # The picker is NOT inlined on the dashboard (it's the popup only).
    assert '<select id="reg-sel"' not in body


async def test_edit_picker_is_a_popup_with_only_enabled_regions(as_user):
    body = (await as_user.get("/me/regions/edit")).text
    # It's the popup modal (same pattern as the capability modal), and saving
    # swaps the inline #regions block back in (the JS then closes the modal).
    assert 'class="modal-overlay"' in body
    assert 'hx-post="/me/regions"' in body and 'hx-target="#regions"' in body
    assert '<select id="reg-sel" name="regions" multiple' in body
    # Default enabled = AS/EU/NA only — Wynn doesn't proxy the rest.
    for c in (AS, EU, NA):
        assert f'value="{c.value}"' in body
    for c in (AF, AN, OC, SA):
        assert f'value="{c.value}"' not in body
    assert body.count("selected") == 2  # Wenweia's EU + NA, pre-selected


async def test_post_saves_enabled_and_clamps_disabled(as_user, seeded):
    player = seeded["players"]["Wenweia"]

    # Enabled codes round-trip canonically...
    r = await as_user.post("/me/regions", data={"regions": ["na", "EU", "na"]})
    assert r.status_code == 200 and "Preferred region(s) updated." in r.text
    await player.refresh_from_db()
    assert player.preferred_regions == "EU,NA"

    # ...a disabled region (SA) in a crafted POST is dropped, not stored.
    r = await as_user.post("/me/regions", data={"regions": ["SA", "EU"]})
    assert r.status_code == 200
    await player.refresh_from_db()
    assert player.preferred_regions == "EU"

    # Empty selection -> "" -> "Any region".
    r = await as_user.post("/me/regions", data={})
    assert r.status_code == 200 and "Any region" in r.text
    fresh = await AnniPlayer.get(mc_uuid=player.mc_uuid)
    assert fresh.preferred_regions == ""


async def test_stored_disabled_region_still_renders_in_read_view(
    client, seeded
):
    """Trixomaniac is seeded OC (not in the default enabled set): the read
    pill must still show it — only the *picker* is filtered."""
    tx = seeded["players"]["Trixomaniac"]
    assert tx.preferred_regions == "OC"
    client.cookies.set(
        "anni_session",
        deps._serializer.dumps(
            {"kind": "user", "mc_uuid": tx.mc_uuid, "name": tx.mc_username}
        ),
    )
    body = (await client.get("/me")).text
    assert 'title="Oceania"' in body and ">🌏 OC<" in body


async def test_regions_endpoints_require_login(client):
    for method, path in (
        ("get", "/me/regions"),
        ("get", "/me/regions/edit"),
        ("post", "/me/regions"),
    ):
        r = await getattr(client, method)(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
