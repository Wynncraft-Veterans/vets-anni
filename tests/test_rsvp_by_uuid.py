"""``app.domain.rsvp_by_uuid`` — UUID-keyed in-game RSVP entrypoint.

Mirrors ``test_rsvp.py``'s pure-domain coverage style. The cog-level
``_post_public`` is exercised via a ``bot=None`` no-op (the rest of the
chain is identical to the cog's ``_do_set`` / ``_do_revoke`` which
``test_rsvp.py`` already covers).
"""

from __future__ import annotations

import time as _time

import pytest

from app.constants import AttendanceNotice
from app.db.models import AnniPlayer, BoardPlacement, Rsvp
from app.domain.rsvp_by_uuid import UuidRsvpError, execute_uuid_rsvp


async def test_hard_sets_row_and_auto_places(seeded):
    p = seeded["players"]["baz"]  # baz has no seeded RSVP/placement

    outcome = await execute_uuid_rsvp(None, p.mc_uuid, "hard")

    row = await Rsvp.filter(event=seeded["event"], player=p).first()
    assert row is not None
    assert row.notice is AttendanceNotice.RSVP_HARD
    assert row.revoked_at is None
    assert outcome.public_message is not None
    assert "**HARD**" in outcome.public_message
    # auto-placed into Unassigned (not late, not walk-in)
    placement = await BoardPlacement.filter(
        event=seeded["event"], player=p
    ).first()
    assert placement is not None


async def test_soft_sets_row_with_soft_label(seeded):
    p = seeded["players"]["baz"]

    outcome = await execute_uuid_rsvp(None, p.mc_uuid, "soft")

    row = await Rsvp.filter(event=seeded["event"], player=p).first()
    assert row is not None
    assert row.notice is AttendanceNotice.RSVP_SOFT
    assert outcome.public_message is not None
    assert "**SOFT**" in outcome.public_message


async def test_revoke_soft_deletes_existing_row(seeded):
    # Wenweia is seeded with HARD; revoke should soft-delete and announce.
    p = seeded["players"]["Wenweia"]

    outcome = await execute_uuid_rsvp(None, p.mc_uuid, "revoke")

    row = await Rsvp.filter(event=seeded["event"], player=p).first()
    assert row is not None
    assert row.revoked_at is not None
    assert outcome.public_message == f"`{p.mc_username}` withdrew their RSVP."


async def test_revoke_with_no_rsvp_is_silent(seeded):
    # baz has no seeded RSVP — revoke is a no-op + silent in Discord.
    p = seeded["players"]["baz"]

    outcome = await execute_uuid_rsvp(None, p.mc_uuid, "revoke")

    assert outcome.public_message is None


async def test_t_minus_90_cutoff_refuses_hard_soft(seeded, monkeypatch):
    from app.services import hot_window

    monkeypatch.setattr(hot_window, "is_rsvp_closed", lambda event, **_: True)
    p = seeded["players"]["baz"]

    with pytest.raises(UuidRsvpError) as exc:
        await execute_uuid_rsvp(None, p.mc_uuid, "hard")
    assert exc.value.status_code == 409
    assert "closed" in exc.value.detail


async def test_t_minus_90_cutoff_still_permits_revoke(seeded, monkeypatch):
    from app.services import hot_window

    monkeypatch.setattr(hot_window, "is_rsvp_closed", lambda event, **_: True)
    p = seeded["players"]["Wenweia"]

    outcome = await execute_uuid_rsvp(None, p.mc_uuid, "revoke")

    assert outcome.public_message is not None


async def test_no_active_event_raises_404(db, monkeypatch):
    from app.domain import rsvp_by_uuid

    async def _no_event():
        return None

    monkeypatch.setattr(rsvp_by_uuid, "get_active_event", _no_event)
    with pytest.raises(UuidRsvpError) as exc:
        await execute_uuid_rsvp(None, "11111111-2222-3333-4444-555555555555", "hard")
    assert exc.value.status_code == 404


async def test_creates_placeholder_for_unknown_uuid(seeded):
    new_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert await AnniPlayer.filter(mc_uuid=new_uuid).first() is None

    outcome = await execute_uuid_rsvp(None, new_uuid, "hard")

    player = await AnniPlayer.filter(mc_uuid=new_uuid).first()
    assert player is not None
    assert player.is_placeholder is True
    # Same uuid[:8] fallback auto_promoter uses; the public message names it.
    assert outcome.public_message is not None
    assert player.mc_username == new_uuid[:8]
    assert f"`{new_uuid[:8]}`" in outcome.public_message
