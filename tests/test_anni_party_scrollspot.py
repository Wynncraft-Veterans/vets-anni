"""S5 ``POST /api/internal/anni-party-scrollspot`` — a party host's scroll spot.

Covers the host writing and clearing the spot, the two 403s (a placed member
who is not the host, and a player with no party on the active board), the
400 for a malformed spot, and secret-gating. The placement lookup filters on
``party_id__isnull``; ``party__isnull`` reads ``isnull`` as a Party field and
raised ``FieldError`` (a 500) on every call, seen live on 2026-10-25.
"""

from __future__ import annotations

import pytest

from app.db.models import Party
from app.settings import get_settings


SECRET = "test-anni-internal"
URL = "/api/internal/anni-party-scrollspot"


@pytest.fixture(autouse=True)
def _wire_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "anni_introspect_secret", SECRET)


async def _party_one_hosted_by(player) -> Party:
    """The seed's party 1, re-hosted by ``player`` (placed in it). The seed's
    own hosts are not placed in the parties they host."""
    party = await Party.get(ordinal=1)
    party.host = player
    await party.save(update_fields=["host_id"])
    return party


async def _post(client, actor_mc_uuid, spot, secret=SECRET):
    return await client.post(
        URL,
        headers={"X-Introspect-Secret": secret},
        json={"actor_mc_uuid": actor_mc_uuid, "scroll_spot": spot},
    )


async def test_host_sets_then_clears_the_spot(client, seeded):
    wen = seeded["players"]["Wenweia"]
    party = await _party_one_hosted_by(wen)

    res = await _post(client, wen.mc_uuid, {"x": 345, "y": 45, "z": -1315})
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    await party.refresh_from_db()
    assert (party.scroll_spot_x, party.scroll_spot_y, party.scroll_spot_z) == (345, 45, -1315)

    res = await _post(client, wen.mc_uuid, None)
    assert res.status_code == 200
    await party.refresh_from_db()
    assert (party.scroll_spot_x, party.scroll_spot_y, party.scroll_spot_z) == (None, None, None)


async def test_placed_member_who_is_not_host_is_refused(client, seeded):
    await _party_one_hosted_by(seeded["players"]["Wenweia"])
    nazzae = seeded["players"]["Nazzae"]  # placed in party 1, not its host

    res = await _post(client, nazzae.mc_uuid, None)
    assert res.status_code == 403
    assert res.json()["detail"] == "only the party host can set scroll_spot"


async def test_player_with_no_party_is_refused(client, seeded):
    metrafish = seeded["players"]["Metrafish"]  # in the Unassigned bucket, no party

    res = await _post(client, metrafish.mc_uuid, None)
    assert res.status_code == 403
    assert res.json()["detail"] == "actor is not in a party for the active event"


async def test_unknown_actor_is_refused(client, seeded):
    res = await _post(client, "00000000-0000-0000-0000-000000000000", None)
    assert res.status_code == 403


async def test_malformed_spot_is_a_400(client, seeded):
    wen = seeded["players"]["Wenweia"]
    res = await _post(client, wen.mc_uuid, {"x": 1, "y": "high", "z": 3})
    assert res.status_code == 400


async def test_wrong_secret_is_a_401(client, seeded):
    wen = seeded["players"]["Wenweia"]
    res = await _post(client, wen.mc_uuid, None, secret="nope")
    assert res.status_code == 401
