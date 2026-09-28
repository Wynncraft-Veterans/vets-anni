"""Staff/board render + REST-twin smoke (the Phase-2 web surface).

Mirrors test_dashboard_smoke: catches Jinja/context regressions and the
colourblind hard-rule (glyph + aria-label + status lamps present regardless of
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
    # No "Configs" heading — the switches label themselves, and the heading
    # was what made this card taller than the legend it sits beside.
    assert "<h3>Configs</h3>" not in r.text
    assert 'aria-label="Board display options"' in r.text   # region still named
    assert "Role/Status info" in r.text
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
    # Buckets render all three Unassigned sub-buckets: the seed populates
    # main (Metrafish & co.), walk-in (Faulischlumpf/baz), and LATE
    # (Salted/Jumla) so every lane has at least one card on demo boots.
    assert "Walk-in sub-bucket" in body
    assert "Soft RSVP sub-bucket" in body   # the derived lane, above walk-ins
    assert "Late sub-bucket" not in body    # retired: lateness is a badge now
    assert "Sitting out" in body
    # The names that drive each sub-bucket also land on the page (so a
    # missing-row regression in the seed surfaces as a failing assertion,
    # not a silently empty dropzone).
    assert "Faulischlumpf" in body and "Salted" in body
    # Colour is NEVER the only signal: glyph + aria-label + status lamps.
    assert 'class="status-lamps"' in body
    assert "aria-label=" in body
    assert "PRIM" in body                # a role glyph
    # Legend renders the status key (border colour; cb: lamps) but the
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



async def test_capability_pips_carry_reliability(as_staff, seeded):
    """Each pip's ring/fade is its reliability — a data attribute the CSS
    keys on, spelled out in the aria-label, and keyed in the legend."""
    import re

    body = (await as_staff.get("/staff/board")).text
    # Wenweia's primary (12 wins, high confidence) and _akaPasta's tank (a
    # rough month in the seed) sit at opposite ends.
    assert 'aria-label="Primary (boss killer) (capable, high reliability)"' in body
    assert 'aria-label="Tank (capable, low reliability)"' in body
    pips = re.findall(r'class="cap-dot"[^>]*data-reliability="(\w+)"', body)
    assert pips and set(pips) <= {"low", "moderate", "high"}
    assert 'class="legend-pips"' in body
    # The two-letter code is always in the DOM — it is the whole pip under cb.
    assert '<span class="cap-letter" aria-hidden="true">PR</span>' in body


async def test_every_card_carries_the_pip_bar(as_staff, seeded):
    """The row is emitted on every card — empty (and flagged) when there is
    nothing to show — so cb can draw the same bar on all of them."""
    body = (await as_staff.get("/staff/board")).text
    cards = body.count('class="person status-border"')
    assert cards and body.count('class="cap-dots"') == cards
    assert "data-empty" in body     # the seed has cards with no capabilities


async def test_cb_reliability_legend_is_its_own_example(as_staff, seeded):
    """Under cb the pip row is one box of codes lined by reliability, so the
    legend is one such box carrying each level's word, not shapes."""
    import re

    plain = (await as_staff.get("/staff/board")).text
    assert "cap-tri" in plain
    assert not re.search(r'class="cap-letter">\s*Moderate', plain)

    as_staff.cookies.set("cb", "1")
    legend = (await as_staff.get("/staff/board")).text
    legend = legend[legend.index('class="legend-pips"'):]
    legend = legend[:legend.index("legend-block")]
    for word in ("Low", "Moderate", "High"):
        assert re.search(rf'class="cap-letter">\s*{word}\s*<', legend)
    assert legend.count('class="cap-dots"') == 1     # one box, not three
    assert "cap-tri" not in legend


async def test_cb_role_legend_keys_the_pip_codes(as_staff, seeded):
    """The pips are bare codes under cb, so the role row must show the same
    codes — a PRIM swatch there would leave "PR" on a card undecoded."""
    from app.constants import ROLE_STYLES

    plain = (await as_staff.get("/staff/board")).text
    assert 'class="role-code"' not in plain

    as_staff.cookies.set("cb", "1")
    body = (await as_staff.get("/staff/board")).text
    legend = body[body.index('class="card legend"'):body.index('class="legend-pips"')]
    for s in ROLE_STYLES.values():
        assert f'<span class="role-code">{s.code}</span>' in legend
        assert f">{s.glyph}</span>" not in legend       # the swatch is gone

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
    # Derived reliability replaced the self-declared build quality.
    assert "Reliability: high" in body    # Wenweia's primary: 12 wins
    assert "Reliability: low" in body     # _akaPasta's tank: a rough month
    assert "Build" not in body


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
    assert body.index('id="board-modal-mount"') > body.index("</main>")  # not clipped by .page

    r = await as_staff.get("/staff/board/add")
    assert r.status_code == 200
    assert "modal-overlay" in r.text and 'name="ign"' in r.text
    assert 'hx-post="/staff/board/player-add"' in r.text


async def test_add_modal_is_staff_gated(client, seeded):
    r = await client.get("/staff/board/add", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/staff"


async def test_status_traffic_light_replaces_the_border_under_cb_only(
    as_staff, seeded
):
    """CB off => a solid coloured border and the lamps hidden; CB on => no
    border and the lamps shown. The lamps are in the DOM either way, one
    set per card, lit to match that card's status."""
    import re

    from app.constants import STATUS_STYLES

    cbc = (await as_staff.get("/static/css/colourblind.css")).text
    anni = (await as_staff.get("/static/css/anni.css")).text
    assert ".status-border { border-width: 3px; border-style: solid; }" in cbc
    assert "body.cb .status-border { border: 0; }" in cbc
    assert ".status-lamps { display:none; }" in anni
    assert "body.cb .status-lamps {" in cbc

    body = (await as_staff.get("/staff/board")).text
    cards = body.count('class="person status-border"')
    lamp_sets = re.findall(
        r'<span class="status-lamps"[^>]*aria-label="Status: ([^"]+)"[^>]*>(.*?)</span>',
        body, re.S)
    assert cards and len(lamp_sets) == cards
    by_label = {s.label: s.lamps for s in STATUS_STYLES.values()}
    for label, inner in lamp_sets:
        lit = "".join("1" if 'class="lit"' in i else "0"
                      for i in re.findall(r"<i[^>]*>", inner))
        assert lit == by_label[label], (label, lit)


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


# --- getting a mistaken add back off the board / out of the DB -------------
#
# "Add Players" get-or-creates an AnniPlayer for whatever IGN resolved, so a
# typo that happens to be a real Minecraft name used to be permanent. One
# surface undoes it now: the roles dashboard's Delete profile. The board card
# used to carry a "remove from board" ✕ too; it was redundant with dragging
# someone to Sitting out, so it went.


async def test_person_card_has_no_remove_from_board_control(as_staff, seeded):
    """Taking a card off the board is what Sitting out is for. The ✕ (and the
    whole PLAYER_REMOVE path behind it) is gone — assert the route too, not
    just the button, so a half-revert can't leave a live mutation endpoint
    with no UI."""
    body = (await as_staff.get("/staff/board")).text
    assert "person-remove" not in body
    assert "/staff/board/player-remove" not in body

    r = await as_staff.post("/staff/board/player-remove",
                            data={"player_uuid": "anyone"})
    assert r.status_code == 404


async def test_roles_row_offers_delete_for_every_profile(as_staff, seeded):
    body = (await as_staff.get("/staff/roles")).text
    baz = seeded["players"]["baz"]            # no caps / RSVP / password
    wen = seeded["players"]["Wenweia"]        # caps + RSVP

    # Both — the restriction to empty shells is gone.
    assert f'/staff/roles/player/{baz.mc_uuid}/delete' in body
    assert f'/staff/roles/player/{wen.mc_uuid}/delete' in body
    # What guards the destructive one is the confirmation naming what goes.
    assert "This CANNOT be undone" in body
    assert "It holds nothing" in body       # ...and the harmless one says so


async def test_roles_delete_purges_the_profile_and_empties_the_row(as_staff, seeded):
    from app.db.models import AnniPlayer

    baz = seeded["players"]["baz"]
    r = await as_staff.post(f"/staff/roles/player/{baz.mc_uuid}/delete")

    assert r.status_code == 200
    assert r.text.strip() == ""   # outerHTML swap with no body removes the row
    assert not await AnniPlayer.filter(mc_uuid=baz.mc_uuid).exists()


async def test_roles_delete_destroys_a_profile_that_holds_real_data(
    as_staff, seeded
):
    """The case the shells-only rule used to block, which is the case staff
    actually need: a profile a typo has already attached an RSVP and
    capabilities to. It goes, and so does everything hanging off it."""
    from app.db.models import AnniPlayer, RoleCapability, Rsvp

    wen = seeded["players"]["Wenweia"]
    assert await RoleCapability.filter(player=wen).exists()
    assert await Rsvp.filter(player=wen).exists()

    r = await as_staff.post(f"/staff/roles/player/{wen.mc_uuid}/delete")

    assert r.status_code == 200
    assert r.text.strip() == ""   # the row goes, same as any other delete
    assert not await AnniPlayer.filter(mc_uuid=wen.mc_uuid).exists()
    assert not await RoleCapability.filter(player_id=wen.mc_uuid).exists()
    assert not await Rsvp.filter(player_id=wen.mc_uuid).exists()


async def test_roles_delete_of_an_unknown_uuid_returns_the_list(as_staff, seeded):
    """The only refusal left. It has no row to swap back, so it bounces."""
    r = await as_staff.post("/staff/roles/player/no-such-uuid/delete",
                            follow_redirects=False)
    assert r.status_code == 303


async def test_roles_delete_is_staff_gated(client, seeded):
    baz = seeded["players"]["baz"]
    r = await client.post(f"/staff/roles/player/{baz.mc_uuid}/delete",
                          follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/staff"


async def test_person_card_carries_the_rsvp_badge(as_staff, seeded):
    """The RSVP axis renders as its own badge before the avatar, with the
    glyph + title + aria-label doing the work (colour is never the only
    signal), and the legend carries the key for all four states."""
    body = (await as_staff.get("/staff/board")).text
    assert "rsvp-flag" in body
    assert 'data-rsvp="hard"' in body     # Wenweia & co.
    assert 'data-rsvp="soft"' in body     # Paradrex / Trixomaniac
    assert 'data-rsvp="none"' in body     # the walk-ins
    assert 'aria-label="RSVP: Hard RSVP"' in body
    assert "var(--rsvp-hard)" in body     # hue via the CB-swappable alias
    # Legend key names every state so the badge is decodable.
    for label in ("Hard RSVP", "Soft RSVP", "No RSVP", "RSVP retracted"):
        assert label in body, label
    # The badge is the leftmost element of the card, ahead of the face.
    card = body[body.index('class="person status-border'):]
    assert card.index("rsvp-flag") < card.index("person-face")


async def test_board_renders_the_soft_rsvp_lane(as_staff, seeded):
    """The derived Unassigned lane renders end to end: every soft RSVP lands
    in the ``offsoft`` dropzone regardless of presence, carved out of the
    main lane above it."""
    body = (await as_staff.get("/staff/board/fragment")).text

    # The Unassigned column, sliced into its three labelled lanes in order.
    unassigned = body[body.index('aria-label="Unassigned (on time)"'):]
    main_lane, rest = unassigned.split("Soft RSVP sub-bucket", 1)
    soft_lane, walkin_lane = rest.split("Walk-in sub-bucket", 1)
    assert 'class="dropzone offsoft"' in soft_lane
    # Both seeded soft RSVPs, and neither is online in this fixture.
    assert "Paradrex" in soft_lane and "Trixomaniac" in soft_lane
    assert "Paradrex" not in main_lane
    # Everyone else stayed put — the lane is a carve-out, not a stampede.
    assert "Metrafish" in main_lane
    assert "Faulischlumpf" in walkin_lane


async def test_gone_stamp_outranks_the_late_hourglass(as_staff, seeded):
    """Both facts stay in the view model — they are independent — but only
    the more urgent stamp is drawn. "Not here" is what an organiser acts on;
    "was late getting here" is water under the bridge. And because is_gone is
    derived from live presence, logging back on restores the hourglass with
    no state to unwind."""
    from main import app
    from app.constants import PresenceStatus
    from app.db.models import BoardPlacement

    para = seeded["players"]["Paradrex"]
    placement = await BoardPlacement.get(event=seeded["event"], player=para)
    placement.is_late = True
    await placement.save(update_fields=["is_late"])

    def card_of(body: str) -> str:
        """One card's markup: from its data-uuid to the next card's. A fixed
        slice is not enough — the RSVP ticket alone is ~1.5kB of inline SVG."""
        rest = body[body.index(f'data-uuid="{para.mc_uuid}"'):]
        nxt = rest.find("data-uuid=", 10)
        return rest if nxt == -1 else rest[:nxt]

    st = app.state.appstate
    st.presence_by_uuid = {para.mc_uuid: PresenceStatus.OFFLINE.value}
    st.seen_online_uuids = {para.mc_uuid}
    try:
        card = card_of((await as_staff.get("/staff/board/fragment")).text)
        assert "gone-mark" in card and "late-mark" not in card

        # They come back: gone clears, the hourglass returns by itself.
        st.presence_by_uuid = {
            para.mc_uuid: PresenceStatus.ONLINE_ELSEWHERE.value}
        card = card_of((await as_staff.get("/staff/board/fragment")).text)
        assert "late-mark" in card and "gone-mark" not in card
    finally:
        st.presence_by_uuid = {}
        st.seen_online_uuids = set()



# --- staff reliability override (/staff/roles) -------------------------------

async def _paradrex_cap(seeded):
    from app.db.models import RoleCapability
    return await RoleCapability.get(player=seeded["players"]["Paradrex"])


async def _post_cap(as_staff, cap, **form):
    return await as_staff.post(
        f"/staff/roles/capability/{cap.id}",
        data={"confidence": cap.confidence.value, "weapons": "Idol", **form},
    )


async def test_staff_modal_offers_the_reliability_override(as_staff, seeded):
    cap = await _paradrex_cap(seeded)
    body = (await as_staff.get(f"/staff/roles/capability/{cap.id}/edit")).text
    assert "Reliability override" in body
    assert '<option value="keep" selected>No change</option>' in body
    assert "Currently <strong>Low</strong>, from their record" in body
    assert 'value="auto"' not in body  # nothing to clear yet


async def test_staff_modal_checks_the_build_on_the_staff_surface(as_staff, seeded):
    """The shared modal must not fall back to /me/capability/check here — a
    staff session isn't a user session, and the owner's levels are the ones
    that matter."""
    cap = await _paradrex_cap(seeded)
    body = (await as_staff.get(f"/staff/roles/capability/{cap.id}/edit")).text
    assert f'hx-get="/staff/roles/capability/{cap.id}/check"' in body
    r = await as_staff.get(f"/staff/roles/capability/{cap.id}/check",
                           params={"weapons": ""})
    assert r.status_code == 200 and "double check" not in r.text


async def test_staff_restart_rebases_and_clear_undoes_it(as_staff, seeded):
    cap = await _paradrex_cap(seeded)  # moderate confidence, 1 win -> Low

    r = await _post_cap(as_staff, cap, reliability_override="high")
    assert r.status_code == 200
    assert "Reliability: high" in r.text and "staff-set" in r.text
    await cap.refresh_from_db()
    assert cap.reliability_override == "high"
    assert cap.reliability_set_wins == 1 and cap.reliability_set_at is not None

    # Re-saving with the default leaves the restart exactly where it was.
    set_at = cap.reliability_set_at
    await _post_cap(as_staff, cap)
    await cap.refresh_from_db()
    assert cap.reliability_override == "high" and cap.reliability_set_at == set_at

    r = await _post_cap(as_staff, cap, reliability_override="auto")
    assert "Reliability: low" in r.text and "staff-set" not in r.text
    await cap.refresh_from_db()
    assert cap.reliability_override is None and cap.reliability_set_at is None


async def test_players_cannot_override_their_own_reliability(client, seeded):
    """The user's own edit route has no such field — a crafted POST is
    ignored and the user modal never offers it."""
    from app.db.models import RoleCapability

    wen = seeded["players"]["Wenweia"]
    client.cookies.set("anni_session", deps._serializer.dumps(
        {"kind": "user", "mc_uuid": wen.mc_uuid, "name": wen.mc_username}
    ))
    cap = await RoleCapability.get(player=wen, role="healer")

    body = (await client.get(f"/me/capability/{cap.id}/edit")).text
    assert "Reliability override" not in body

    r = await client.post(f"/me/capability/{cap.id}", data={
        "confidence": "moderate", "weapons": "Lament",
        "reliability_override": "high",
    })
    assert r.status_code == 200
    await cap.refresh_from_db()
    assert cap.reliability_override is None
