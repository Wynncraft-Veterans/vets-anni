"""Logged-in render smoke — the templates Phase 1 added actually render.

The unit tests cover the logic; this catches Jinja/context regressions
(macro-vs-context name clashes, lazy-relation access, the seeded
future-event Specific branch) before they reach a browser. Lifespan is
skipped by ASGITransport, so pollers never run — the empty AppState is the
realistic "upstream not yet polled" path the templates must survive.
"""

from __future__ import annotations

import pytest_asyncio

from app.web import deps


@pytest_asyncio.fixture
async def as_user(client, seeded):
    """``client`` signed in as a seeded player with rich data (Wenweia: 2
    capabilities, a party placement, a hard RSVP). Cookie set on the client
    (not per-request) so httpx doesn't warn about ambiguous persistence."""
    player = seeded["players"]["Wenweia"]
    client.cookies.set(
        "anni_session",
        deps._serializer.dumps(
            {"kind": "user", "mc_uuid": player.mc_uuid, "name": player.mc_username}
        ),
    )
    return client


async def test_me_dashboard_renders_general_and_specific(as_user):
    r = await as_user.get("/me")
    assert r.status_code == 200
    body = r.text
    assert "Wenweia's Dashboard" in body
    assert "Your Indicated Capabilities" in body
    assert "Your Registration Status" in body
    # Seeded event is ~93 min out (future) -> Today's-anni module NOT blank.
    assert "Today's Annihilation" in body
    assert "RSVP" in body and "Tentative Information" in body
    # The 15 s refresh lives on the STABLE wrapper (card animates once, the
    # fragment swaps inside it) — not on the fragment itself.
    assert 'hx-get="/me/specific"' in body
    # Role chip emits its glyph + aria-label regardless of colour (CB rule).
    assert "aria-label" in body and "PRIM" in body


async def test_specific_fragment_is_inner_only(as_user):
    """GET /me/specific returns just the inner content (no card/hx) so the
    innerHTML swap can't re-fade or duplicate the card."""
    r = await as_user.get("/me/specific")
    assert r.status_code == 200
    assert "RSVP" in r.text
    assert 'hx-get="/me/specific"' not in r.text  # hx lives on the wrapper
    assert 'class="card"' not in r.text            # wrapper owns the card


async def test_capability_modal_quotes_guidance_and_links(as_user):
    r = await as_user.get("/me/capability/new")
    assert r.status_code == 200
    assert "wynnvets.org/docs/guild/anni" in r.text  # docs links (spec)
    assert "Requirements:" in r.text


async def test_me_redirects_anonymous_to_login(client):
    r = await client.get("/me", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_specific_fragment_hx_redirects_on_expired_session(client):
    """Polling-fragment session expiry must NOT swap a full login page into
    #participation. The dashboard's 15 s hx-get follows redirects transparently,
    so a plain 303 would inject login.html (nav + sign-in form) into the
    Participation Status card — producing a dashboard-inside-itself. Emit
    HX-Redirect so htmx does a top-level navigation (which kills the poll)."""
    r = await client.get(
        "/me/specific",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 204
    assert r.headers.get("HX-Redirect") == "/"
    assert r.text == ""


async def test_staff_page_shows_login_when_signed_out(client):
    r = await client.get("/staff")
    assert r.status_code == 200
    assert "Staff Login" in r.text and "Staff password" in r.text
