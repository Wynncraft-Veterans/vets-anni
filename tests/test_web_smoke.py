"""Phase-0 smoke tests: the dev server boots and every public route renders.

These exercise the same ASGI app the ".vscode: dev server" launch config
runs — just without the file DB / fishbot. They are the early-warning that a
template or route regressed before it ever reaches the browser.
"""

from __future__ import annotations

from app.web import deps


def _login(client, player):
    """Forge a signed user session on the client (overview/me need one)."""
    client.cookies.set(
        "anni_session",
        deps._serializer.dumps(
            {"kind": "user", "mc_uuid": player.mc_uuid, "name": player.mc_username}
        ),
    )


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_pages_render_without_an_event(client):
    """No anni announced: the landing page still renders its empty shell;
    /overview is now login-gated (the landing page is the anon overview)."""
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Not yet announced" in r.text

    r = await client.get("/overview", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_toggle_cb_sets_then_clears_the_cookie(client):
    """The colourblind toggle (a spec hard requirement on every interface)
    flips the ``cb`` cookie and 303s back to ``next``."""
    r = await client.get("/toggle-cb?next=/overview")
    assert r.status_code == 303
    assert r.headers["location"] == "/overview"
    assert r.cookies.get("cb") == "1"

    # Cookie now in the client jar -> a second toggle turns it back off.
    r = await client.get("/toggle-cb?next=/overview")
    assert r.status_code == 303
    assert r.cookies.get("cb") in (None, "")


async def test_pages_render_the_seeded_event(client, seeded):
    """With the dev dataset loaded, the announced-anni branch renders: the
    eager-loaded organiser name and the real countdown target appear."""
    event = seeded["event"]

    r = await client.get("/")
    assert r.status_code == 200
    assert "Holidaze" in r.text
    assert str(event.stamp_epoch) in r.text

    # /overview needs a session now; logged in it shows the generic status.
    _login(client, seeded["players"]["Wenweia"])
    r = await client.get("/overview")
    assert r.status_code == 200
    assert "Holidaze" in r.text          # lead-organiser host pill
    assert "Announced" in r.text
