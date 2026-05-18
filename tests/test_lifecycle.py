"""Event phase calc + the grace-open / grace-wipe lifecycle.

``schedule.phase_of`` is pure (boundary table); the wipe is exercised against
the seeded board so the WIN ``success_count`` credit, the placement/RSVP purge
and the exactly-one-active invariant are all asserted together — the spec's
single-transaction grace-wipe.
"""

from __future__ import annotations

import time

from app.constants import PartyResult
from app.db.lifecycle import get_active_event
from app.db.models import (
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    Rsvp,
)
from app.domain.schedule import EventPhase, is_board_frozen, phase_of
from app.services import lifecycle_task
from app.services.state import AppState
from app.settings import get_settings


def test_phase_of_boundaries():
    stamp, grace = 1_000_000, 7200
    assert phase_of(stamp, grace, now=stamp - 1) is EventPhase.PENDING
    assert phase_of(stamp, grace, now=stamp) is EventPhase.PENDING       # incl.
    assert phase_of(stamp, grace, now=stamp + 1) is EventPhase.GRACE
    assert phase_of(stamp, grace, now=stamp + grace) is EventPhase.GRACE  # incl.
    assert phase_of(stamp, grace, now=stamp + grace + 1) is EventPhase.EXPIRED

    assert is_board_frozen(EventPhase.GRACE) is True
    assert is_board_frozen(EventPhase.PENDING) is False
    assert is_board_frozen(EventPhase.EXPIRED) is False


async def test_grace_opens_once_without_wiping(seeded):
    event = seeded["event"]
    event.stamp_epoch = int(time.time()) - 60  # just started -> GRACE
    await event.save(update_fields=["stamp_epoch"])
    assert event.grace_opened_at is None

    await lifecycle_task._tick(AppState(), get_settings())

    await event.refresh_from_db()
    assert event.grace_opened_at is not None
    assert event.is_active is True and event.wiped_at is None
    assert await BoardPlacement.filter(event=event).exists()  # NOT wiped


async def test_expired_event_wipes_credits_wins_and_clears(seeded):
    event = seeded["event"]
    settings = get_settings()
    grace = settings.grace_hours * 3600

    # Party 1 (Wenweia=PRIMARY, Nazzae=HEALER, _akaPasta=TANK) WON; party 2
    # has no result -> no credit.
    party1 = await Party.get(event=event, ordinal=1)
    party1.result = PartyResult.WIN
    await party1.save(update_fields=["result"])

    event.stamp_epoch = int(time.time()) - grace - 10  # EXPIRED
    await event.save(update_fields=["stamp_epoch"])

    wen_before = (await RoleCapability.get(
        player=seeded["players"]["Wenweia"], role="primary")).success_count
    naz_before = (await RoleCapability.get(
        player=seeded["players"]["Nazzae"], role="healer")).success_count
    players_before = await AnniPlayer.all().count()
    caps_before = await RoleCapability.all().count()

    await lifecycle_task._tick(AppState(), settings)

    # WIN party members' matching capability success_count +1.
    assert (await RoleCapability.get(
        player=seeded["players"]["Wenweia"], role="primary")
    ).success_count == wen_before + 1
    assert (await RoleCapability.get(
        player=seeded["players"]["Nazzae"], role="healer")
    ).success_count == naz_before + 1

    # The event's board + RSVPs are gone; the event is wiped + inactive.
    assert await BoardPlacement.filter(event=event).count() == 0
    assert await Rsvp.filter(event=event).count() == 0
    await event.refresh_from_db()
    assert event.wiped_at is not None and event.is_active is False
    assert await get_active_event() is None

    # Players + capabilities persist across a wipe (only the event is cleared).
    assert await AnniPlayer.all().count() == players_before
    assert await RoleCapability.all().count() == caps_before
