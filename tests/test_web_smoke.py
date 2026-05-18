"""Phase-0 smoke tests: the dev server boots and every public route renders.

These exercise the same ASGI app the ".vscode: dev server" launch config
runs — just without the file DB / fishbot. They are the early-warning that a
template or route regressed before it ever reaches the browser.
"""

from __future__ import annotations


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_pages_render_without_an_event(client):
    """No anni announced: login + overview still render their empty shell."""
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Not yet announced" in r.text

    r = await client.get("/overview")
    assert r.status_code == 200
    assert "No anni announced" in r.text


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

    r = await client.get("/overview")
    assert r.status_code == 200
    assert "Holidaze" in r.text
    assert "Announced" in r.text
