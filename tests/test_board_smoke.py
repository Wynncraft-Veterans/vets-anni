"""Staff/board render + REST-twin smoke (the Phase-2 web surface).

Mirrors test_dashboard_smoke: catches Jinja/context regressions and the
colourblind hard-rule (glyph + aria-label + data-pattern present regardless of
``cb``) before they reach a browser, and proves the no-JS / socket-dropped
REST twins mutate through the same single-instance path. WS *socket* behaviour
is covered against the hub directly in test_ws (the project's test transport,
httpx ASGITransport, deliberately has no websocket/lifespan — see conftest)."""

from __future__ import annotations

import pytest_asyncio

from app.db.models import BoardPlacement
from app.web import deps


@pytest_asyncio.fixture
async def as_staff(client):
    """``client`` with a forged staff session cookie (low-trust model: a
    signed ``kind=staff`` cookie is the whole gate)."""
    client.cookies.set("anni_session",
                        deps._serializer.dumps({"kind": "staff"}))
    return client


async def test_staff_hub_renders_status_and_tools(as_staff, seeded):
    r = await as_staff.get("/staff")
    assert r.status_code == 200
    body = r.text
    assert "Staff hub" in body
    assert "Today's Annihilation" in body
    assert "Holidaze" in body                       # eager-loaded organiser
    assert str(seeded["event"].stamp_epoch) in body  # live countdown target
    assert "Rotate the staff password" in body       # Phase-1 tools kept


async def test_board_renders_people_legend_and_cb_channels(as_staff, seeded):
    r = await as_staff.get("/staff/board")
    assert r.status_code == 200
    body = r.text
    assert "Organizer Board" in body
    assert "Wenweia" in body and "Party 1" in body
    # Buckets incl. the LATE sub-bucket (Salted/Jumla are seeded late).
    assert "Late sub-bucket" in body and "Sitting out" in body
    # Colour is NEVER the only signal: glyph + aria-label + status pattern.
    assert "data-pattern=" in body
    assert "aria-label=" in body
    assert "PRIM" in body                # a role glyph
    assert "Status borders" in body      # the legend explains the non-colour
    # board.js + vendored SortableJS are wired (no CDN/build step).
    assert "board.js" in body and "sortable.min.js" in body


async def test_board_fragment_is_inner_only(as_staff, seeded):
    r = await as_staff.get("/staff/board/fragment")
    assert r.status_code == 200
    assert 'id="board"' in r.text
    assert "<nav" not in r.text and "Organizer Board" not in r.text  # no chrome


async def test_roles_dashboard_lists_capabilities(as_staff, seeded):
    r = await as_staff.get("/staff/roles")
    assert r.status_code == 200
    body = r.text
    assert "Roles dashboard" in body
    assert "Wenweia" in body and "Core" in body
    assert "Labyrinth" in body            # a seeded weapon
    assert "win" in body.lower()          # success-count pill


async def test_rest_move_twin_mutates_through_single_instance(as_staff, seeded):
    """The socket-down fallback still funnels through board_hub -> the one
    (event,player) UPSERT (no duplicate)."""
    event = seeded["event"]
    wen = seeded["players"]["Wenweia"]
    n0 = await BoardPlacement.filter(event=event).count()

    r = await as_staff.post("/staff/board/move", data={
        "player_uuid": wen.mc_uuid, "bucket": "wontassign", "sort_index": 0})
    assert r.status_code == 200
    assert 'id="board"' in r.text
    assert await BoardPlacement.filter(event=event).count() == n0  # moved
    assert (await BoardPlacement.get(event=event,
                                     player=wen)).bucket.value == "wontassign"


async def test_rest_player_add_unknown_ign_shows_friendly_error(as_staff, seeded):
    r = await as_staff.post("/staff/board/player-add", data={"ign": "ghost"})
    assert r.status_code == 200
    # Friendly inline reject, not a 4xx/5xx (apostrophe is HTML-escaped in the
    # rendered fragment, so match an apostrophe-free slice of the reason).
    assert "find a Minecraft account" in r.text
    assert 'class="bar bar-danger"' in r.text


async def test_board_requires_staff(client, seeded):
    for path in ("/staff/board", "/staff/board/fragment", "/staff/roles"):
        r = await client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/staff"
