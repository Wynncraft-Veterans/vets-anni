# >>> PATCH BEGIN: ANNI-250 (2026-10-16) — this whole file is the patch.
"""Anni #250 special-casing (``app/domain/anni_250.py``) — temporary.

Each behaviour is pinned against its neighbours too (#249 / #251), so an
off-by-one in the numbering can't pass as "the special case works".
Delete this file with the rest of the ANNI-250 patch.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from app.constants import AttendanceNotice, PartyResult, SetbackKind
from app.db.models import (
    AnniEvent,
    BoardPlacement,
    Party,
    RoleCapability,
    Rsvp,
    Setback,
)
from app.domain import anni_250


async def _fill(count: int) -> None:
    """``count`` past annis just after ``\\guess``'s anchor (#212, #213, …)."""
    from app.bot.cogs.guess import _ANNI_ANCHOR_EPOCH

    for i in range(count):
        await AnniEvent.create(
            stamp_epoch=_ANNI_ANCHOR_EPOCH + 1 + i, is_active=False,
            wiped_at=datetime.now(timezone.utc),
        )


async def _make_seeded_event(number: int) -> None:
    """Make the seeded active event (the newest row) anni #``number``."""
    from app.bot.cogs.guess import _ANNI_ANCHOR_NUMBER

    await _fill(number - _ANNI_ANCHOR_NUMBER - 1)


async def test_number_agrees_with_guess(seeded):
    from app.bot.cogs import guess

    await _make_seeded_event(250)

    assert await anni_250.number_of(seeded["event"]) == 250
    assert await guess._current_anni_number() == 250
    assert await anni_250.is_anni_250(seeded["event"]) is True
    assert await anni_250.is_anni_250(None) is False


async def test_neighbouring_annis_are_not_special(seeded):
    await _make_seeded_event(249)
    assert await anni_250.is_anni_250(seeded["event"]) is False


# --------------------------------------------------------------------------- #
# \guess                                                                      #
# --------------------------------------------------------------------------- #


class _FakeTempserver:
    def __init__(self, value: int) -> None:
        self._value = value

    async def stamp(self) -> int:
        return self._value


@pytest.mark.parametrize(
    ("logged", "future", "number", "banner"),
    [
        (38, False, 250, True),   # #249 has passed -> predicting #250
        (39, True, 250, True),    # #250 confirmed
        (37, False, 249, False),  # predicting #249
        (38, True, 249, False),   # #249 confirmed
        (39, False, 251, False),  # #250 has passed -> predicting #251
    ],
)
async def test_guess_banner_only_for_250(
    db, monkeypatch, logged, future, number, banner,
):
    from app.bot.cogs import guess as cog

    await _fill(logged)
    stamp = int(time.time()) + (3_600 if future else -86_400)
    monkeypatch.setattr(cog, "get_tempserver", lambda: _FakeTempserver(stamp))

    reply = await cog.execute_guess()

    assert f"Anni #{number}" in reply.text
    assert ("level 30–40 classes" in reply.text) is banner
    assert ("1558562472539979926" in reply.text) is banner
    if banner:
        # A banner on top, not a replacement: the timing is still there.
        assert reply.text.startswith("> # ")
        assert ("is confirmed" if future else "Probable Time") in reply.text
        # One quote box, link included: no bare blank line splits it.
        box = reply.text.split("\n\n")[0]
        assert all(line.startswith("> ") for line in box.splitlines())
        assert "1558562472539979926" in box
    # The box plot rides along on the prediction branch either way.
    assert (reply.image_png is not None) is (not future)


# --------------------------------------------------------------------------- #
# \rsvp + the in-game /wv anni rsvp                                           #
# --------------------------------------------------------------------------- #


class _NoDazebot:
    async def resolve_anni_identity(self, _discord_id):
        raise AssertionError("an Anni #250 refusal needs no identity lookup")


@pytest.mark.parametrize("action", ["hard", "soft"])
async def test_rsvp_refused_for_250(seeded, monkeypatch, action):
    from app.bot.cogs import rsvp as cog

    await _make_seeded_event(250)
    monkeypatch.setattr(cog, "get_dazebot_client", lambda: _NoDazebot())
    before = await Rsvp.all().count()

    outcome = await cog.execute_rsvp(123, action)

    assert anni_250.FORM_URL in outcome.private_message
    assert "level 30–40" in outcome.private_message
    # Timing localises per viewer — a tag, never "today".
    assert (
        f"for the anni <t:{seeded['event'].stamp_epoch}:R>:"
        in outcome.private_message
    )
    assert "today" not in outcome.private_message.lower()
    assert outcome.public_message is None
    assert await Rsvp.all().count() == before


async def test_t_minus_90_cutoff_gives_way_to_250(seeded, monkeypatch):
    """Inside T-90, #250 still points at the form on both surfaces rather
    than saying RSVPs are closed."""
    from app.bot.cogs import rsvp as cog
    from app.domain.rsvp_by_uuid import UuidRsvpError, execute_uuid_rsvp
    from app.services import hot_window

    await _make_seeded_event(250)
    monkeypatch.setattr(hot_window, "is_rsvp_closed", lambda event, **_: True)
    monkeypatch.setattr(cog, "get_dazebot_client", lambda: _NoDazebot())

    outcome = await cog.execute_rsvp(123, "hard")
    assert anni_250.FORM_URL in outcome.private_message
    assert "close" not in outcome.private_message.lower()

    with pytest.raises(UuidRsvpError) as exc:
        await execute_uuid_rsvp(None, seeded["players"]["baz"].mc_uuid, "soft")
    assert anni_250.FORM_URL in exc.value.detail
    assert "closed" not in exc.value.detail


async def test_t_minus_90_cutoff_still_applies_to_249(seeded, monkeypatch):
    from test_rsvp import _ident, _patch_identity

    from app.bot.cogs.rsvp import execute_rsvp
    from app.domain.rsvp_by_uuid import UuidRsvpError, execute_uuid_rsvp
    from app.services import hot_window

    await _make_seeded_event(249)
    monkeypatch.setattr(hot_window, "is_rsvp_closed", lambda event, **_: True)
    baz = seeded["players"]["baz"]
    _patch_identity(monkeypatch, _ident(baz))

    outcome = await execute_rsvp(123, "hard")
    assert "RSVPs close 90 minutes before" in outcome.private_message

    with pytest.raises(UuidRsvpError) as exc:
        await execute_uuid_rsvp(None, baz.mc_uuid, "hard")
    assert exc.value.detail == "RSVP is closed (within 90 min of anni)"


async def test_rsvp_status_still_works_for_250(seeded, monkeypatch):
    from test_rsvp import _ident, _patch_identity

    from app.bot.cogs.rsvp import execute_rsvp

    await _make_seeded_event(250)
    _patch_identity(monkeypatch, _ident(seeded["players"]["Wenweia"]))

    outcome = await execute_rsvp(123, "status")

    assert "Your current RSVP is **HARD**" in outcome.private_message


async def test_rsvp_accepted_for_249(seeded, monkeypatch):
    from test_rsvp import _ident, _patch_identity

    from app.bot.cogs.rsvp import execute_rsvp

    await _make_seeded_event(249)
    baz = seeded["players"]["baz"]
    _patch_identity(monkeypatch, _ident(baz))

    outcome = await execute_rsvp(123, "hard")

    assert "RSVP recorded" in outcome.private_message
    assert await Rsvp.filter(event=seeded["event"], player=baz).exists()


async def test_in_game_rsvp_refused_for_250(seeded):
    from app.domain.rsvp_by_uuid import UuidRsvpError, execute_uuid_rsvp

    await _make_seeded_event(250)
    baz = seeded["players"]["baz"]

    with pytest.raises(UuidRsvpError) as exc:
        await execute_uuid_rsvp(None, baz.mc_uuid, "hard")

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert anni_250.FORM_URL in detail
    assert "level 30–40" in detail
    # Plain chat text: no Discord tag or markdown, the time spelled out
    # (the seeded anni is 93 minutes out).
    assert "<t:" not in detail and "**" not in detail and "](" not in detail
    assert "will not be used for the anni in 2 hours:" in detail
    assert not await Rsvp.filter(event=seeded["event"], player=baz).exists()
    # Withdrawing is still allowed (e.g. an RSVP made before this patch).
    wen = seeded["players"]["Wenweia"]
    outcome = await execute_uuid_rsvp(None, wen.mc_uuid, "revoke")
    assert outcome.public_message is not None


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (20, "in a few seconds"),
        (60, "in a minute"),
        (30 * 60, "in 30 minutes"),
        (60 * 60, "in an hour"),
        (5 * 3600, "in 5 hours"),
        (30 * 3600, "in a day"),
        (3 * 86400, "in 3 days"),
        (-10 * 60, "10 minutes ago"),
    ],
)
def test_in_game_relative_time(offset, expected):
    from app.domain.rsvp_by_uuid import _relative

    assert _relative(int(time.time()) + offset) == expected


# --------------------------------------------------------------------------- #
# Grace-wipe recordkeeping                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("number", "on_record"), [(249, True), (250, False)])
async def test_wipe_keeps_250_off_the_record(seeded, number, on_record):
    """The same board — a WIN, a LOSS and a no-show hard RSVP — records all
    three for #249 and none of them for #250, and wipes either way."""
    from app.services import lifecycle_task
    from app.services.state import AppState
    from app.settings import get_settings

    event = seeded["event"]
    players = seeded["players"]
    await _make_seeded_event(number)

    party1 = await Party.get(event=event, ordinal=1)  # Wenweia PRIMARY
    party1.result = PartyResult.WIN
    await party1.save(update_fields=["result"])
    party2 = await Party.get(event=event, ordinal=2)  # Minethuselah
    party2.result = PartyResult.LOSS
    await party2.save(update_fields=["result"])
    mine = await BoardPlacement.get(event=event, player=players["Minethuselah"])
    mine.assigned_role = "healer"
    await mine.save(update_fields=["assigned_role"])
    await RoleCapability.create(
        player=players["Minethuselah"], role="healer",
        confidence="moderate", success_count=0,
    )
    # Presence tracking ran (Wenweia was seen); baz hard-RSVP'd and wasn't.
    await Rsvp.filter(event=event, player=players["Wenweia"]).update(
        seen_online_at=datetime.now(timezone.utc),
    )
    await Rsvp.create(
        event=event, player=players["baz"], notice=AttendanceNotice.RSVP_HARD,
    )

    wen_before = (await RoleCapability.get(
        player=players["Wenweia"], role="primary")).success_count

    settings = get_settings()
    event.stamp_epoch = int(time.time()) - settings.grace_hours * 3600 - 10
    await event.save(update_fields=["stamp_epoch"])
    await lifecycle_task._tick(AppState(), settings)

    wen_after = (await RoleCapability.get(
        player=players["Wenweia"], role="primary")).success_count
    # This event's only: the seed carries a setback of its own.
    kinds = set(await Setback.filter(event=event).values_list("kind", flat=True))
    if on_record:
        assert wen_after == wen_before + 1
        assert kinds == {SetbackKind.LOSS, SetbackKind.MISSED}
    else:
        assert wen_after == wen_before
        assert kinds == set()
    # Off the record is not "not wiped": the board is cleared regardless.
    await event.refresh_from_db()
    assert event.wiped_at is not None and event.is_active is False
    assert await BoardPlacement.filter(event=event).count() == 0
# <<< PATCH END: ANNI-250
