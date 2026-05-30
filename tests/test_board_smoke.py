"""Staff/board render + REST-twin smoke (the Phase-2 web surface).

Mirrors test_dashboard_smoke: catches Jinja/context regressions and the
colourblind hard-rule (glyph + aria-label + data-pattern present regardless of
``cb``) before they reach a browser, and proves the no-JS / socket-dropped
REST twins mutate through the same single-instance path. WS *socket* behaviour
is covered against the hub directly in test_ws (the project's test transport,
httpx ASGITransport, deliberately has no websocket/lifespan — see conftest)."""

from __future__ import annotations

import pytest_asyncio

from app.db.models import BoardPlacement, Party
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


async def test_label_toggle_default_hidden_off_cb_and_flip(as_staff, seeded):
    """CB off: labels hidden by default (colour conveys it); the Configs box
    renders the combined Role+Status switch and the toggle round-trips a
    cookie that flips both body classes together."""
    r = await as_staff.get("/staff/board")
    assert "hide-rolelabel" in r.text and "hide-statuslabel" in r.text
    assert "<h3>Configs</h3>" in r.text
    assert "Role and Status Labels" in r.text
    assert "cfg-switch" in r.text                  # the switch control
    assert "Hide the text tags" not in r.text      # subheading removed

    r = await as_staff.get("/toggle-label?which=tags&next=/staff/board",
                            follow_redirects=False)
    assert r.status_code == 303 and r.cookies.get("lbl_tags") == "1"

    r = await as_staff.get("/staff/board")
    # The combined switch flips both body classes in lockstep.
    assert "hide-rolelabel" not in r.text
    assert "hide-statuslabel" not in r.text
    assert 'aria-checked="true"' in r.text         # the combined switch is on

    r = await as_staff.get("/toggle-label?which=bogus", follow_redirects=False)
    assert r.status_code == 303  # unknown facet -> safe bounce, no crash


async def test_label_toggle_unlocked_under_cb_and_default_hidden(as_staff, seeded):
    """CB no longer locks the label toggle — it defaults hidden in both modes
    (the role-card background, status border colour+pattern, and capability
    dots still carry the signal) and stays interactive under CB."""
    as_staff.cookies.set("cb", "1")
    r = await as_staff.get("/staff/board")
    assert "hide-rolelabel" in r.text and "hide-statuslabel" in r.text
    assert "cfg-locked" not in r.text and "🔒" not in r.text  # no lock under CB
    assert "/toggle-label?which=tags" in r.text              # row is a real link

    r = await as_staff.get("/toggle-label?which=tags&next=/staff/board",
                            follow_redirects=False)
    assert r.status_code == 303 and r.cookies.get("lbl_tags") == "1"

    r = await as_staff.get("/staff/board")
    assert "hide-rolelabel" not in r.text and "hide-statuslabel" not in r.text


async def test_pin_legend_defaults_on_and_toggles_off(as_staff, seeded):
    """Pin is a config that defaults ON (no cookie == pinned); turning it off
    stores the explicit opt-out and drops the sticky class."""
    r = await as_staff.get("/staff/board")
    assert "legend-wrap pinned" in r.text          # default on
    assert "Pin to top" in r.text

    r = await as_staff.get("/toggle-label?which=pin&next=/staff/board",
                            follow_redirects=False)
    assert r.status_code == 303 and r.cookies.get("cfg_pin") == "0"

    r = await as_staff.get("/staff/board")
    assert "legend-wrap pinned" not in r.text      # opted out
    assert 'class="legend-wrap"' in r.text


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
    # Legend renders the status-border key (glyph+label+pattern) but the
    # "Roles"/"Status borders" headers + the prose subheader were removed.
    assert "legend-status" in body
    assert "<h3>Roles</h3>" not in body
    assert "<h3>Status borders</h3>" not in body
    assert "The border colour" not in body
    # Party-edit fields are always visible now (no <details> collapsible).
    assert "<details" not in body and "Edit party" not in body
    assert 'name="world"' in body and 'name="result"' in body
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


async def test_party_head_trimmed_and_stage_labels_readable(as_staff, seeded):
    r = await as_staff.get("/staff/board")
    body = r.text
    # Tweak: the stage + tbd bubbles are gone from the "Party N x/y" head.
    assert "stage 3/5" not in body          # seeded party 1 was stage 3
    assert "Party 1" in body and "/10" in body   # "Party N  x/10" head kept
    # Stage dropdown shows a readable description, not a bare number.
    assert "Determining how many parties" in body   # PARTY_STAGE_LABELS[2]
    # "+ Party" replaced by a visible "Add Party" accent button.
    assert "Add Party" in body and "+ Party" not in body
    assert "btn-add" in body
    # World capped at 5 chars.
    assert 'name="world"' in body and 'maxlength="5"' in body


async def test_add_player_is_a_popup_not_an_inline_field(as_staff, seeded):
    body = (await as_staff.get("/staff/board")).text
    # The always-on input is gone; the Players head has a popup trigger.
    assert "Add a walk-in by IGN" not in body
    assert 'hx-get="/staff/board/add"' in body
    assert 'id="board-modal-mount"' in body

    r = await as_staff.get("/staff/board/add")
    assert r.status_code == 200
    assert "modal-overlay" in r.text and 'name="ign"' in r.text
    assert 'hx-post="/staff/board/player-add"' in r.text


async def test_add_modal_is_staff_gated(client, seeded):
    r = await client.get("/staff/board/add", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/staff"


async def test_status_border_patterns_are_cb_only(client):
    """CB off => a single solid coloured outline; the dash/dot/double
    patterns are scoped under body.cb (and data-pattern is still always in
    the DOM, asserted elsewhere). Guards tweak: 'solid outline when cb off'."""
    r = await client.get("/static/css/colourblind.css")
    assert r.status_code == 200
    css = r.text
    # The base rule is a plain solid border (cb-off default).
    assert ".status-border { border-width: 3px; border-style: solid; }" in css
    # Every pattern selector is cb-scoped — none appears unscoped.
    assert "body.cb .status-border[data-pattern=" in css
    for line in css.splitlines():
        s = line.strip()
        if s.startswith(".status-border[data-pattern="):
            raise AssertionError(f"unscoped pattern rule leaks with cb off: {s}")


async def test_party_collapse_is_per_user_cookie_and_survives_refresh(
    as_staff, seeded
):
    """Collapsing a party hides its edit form + members to one line, persists
    via a cookie (so it survives the WS-driven #board refreshes), and toggles
    back. It's a personal view pref — server-rendered, no board_hub."""
    p1 = await Party.get(event=seeded["event"], ordinal=1)
    pid = str(p1.id)
    url = f"/staff/board/party/{pid}/collapse"

    r = await as_staff.get("/staff/board")
    assert r.text.count('class="party-set"') == 2          # 2 seeded parties
    assert f'hx-get="{url}"' in r.text                      # the toggle exists

    r = await as_staff.get(url, follow_redirects=False)
    assert r.status_code == 200 and 'id="board"' in r.text  # the #board frag
    assert r.cookies.get("collapsed_parties") == pid
    assert r.text.count('class="party-set"') == 1           # party 1 collapsed
    assert "party-summary" in r.text                        # one-line summary

    # Survives a plain fragment refresh (the WS path) — cookie is now in the
    # jar, the server re-renders it collapsed without re-toggling.
    r = await as_staff.get("/staff/board/fragment")
    assert r.text.count('class="party-set"') == 1

    # Toggling again expands it and clears the (now empty) cookie.
    r = await as_staff.get(url, follow_redirects=False)
    assert r.text.count('class="party-set"') == 2
    assert r.cookies.get("collapsed_parties") in (None, "")


async def test_party_collapse_is_staff_gated(client, seeded):
    p1 = await Party.get(event=seeded["event"], ordinal=1)
    r = await client.get(f"/staff/board/party/{p1.id}/collapse",
                         follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/staff"


async def test_board_requires_staff(client, seeded):
    for path in ("/staff/board", "/staff/board/fragment", "/staff/roles"):
        r = await client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/staff"
