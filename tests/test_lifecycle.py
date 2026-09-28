"""Event phase calc + the grace-open / grace-wipe lifecycle.

``schedule.phase_of`` is pure (boundary table); the wipe is exercised against
the seeded board so the WIN ``success_count`` credit, the placement/RSVP purge
and the exactly-one-active invariant are all asserted together — the spec's
single-transaction grace-wipe.
"""

from __future__ import annotations

import time

from datetime import datetime, timezone

from app.constants import BucketKind, PartyResult, Role, SetbackKind
from app.db.lifecycle import get_active_event
from app.db.models import (
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    Rsvp,
    Setback,
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
    # has one member (Minethuselah) with no assigned role, so its result
    # doesn't matter for the credit assertions below.
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


async def test_expired_event_credits_tbd_parties_as_wins(seeded):
    """Staff forgot to record a result before the grace-wipe fired: the
    default-to-WIN rule (see ``_credit_wins``) credits members anyway. Only
    an explicit ``LOSS``/``LAG`` denies credit."""
    event = seeded["event"]
    settings = get_settings()
    grace = settings.grace_hours * 3600

    # Party 1 is left at TBD (staff never set it); party 2 is explicitly LOSS
    # so its members must NOT be credited even under the default-to-WIN rule.
    party1 = await Party.get(event=event, ordinal=1)
    assert party1.result is PartyResult.TBD  # invariant the test rests on
    party2 = await Party.get(event=event, ordinal=2)
    party2.result = PartyResult.LOSS
    await party2.save(update_fields=["result"])

    # Give Minethuselah (party 2's only member) a HEALER role + capability so
    # the LOSS-denies-credit check has something concrete to assert.
    mine_place = await BoardPlacement.get(
        event=event, player=seeded["players"]["Minethuselah"],
    )
    mine_place.assigned_role = "healer"
    await mine_place.save(update_fields=["assigned_role"])
    mine_cap = await RoleCapability.create(
        player=seeded["players"]["Minethuselah"], role="healer",
        confidence="moderate", success_count=0,
    )

    event.stamp_epoch = int(time.time()) - grace - 10  # EXPIRED
    await event.save(update_fields=["stamp_epoch"])

    wen_before = (await RoleCapability.get(
        player=seeded["players"]["Wenweia"], role="primary")).success_count

    await lifecycle_task._tick(AppState(), settings)

    # Party 1's TBD counted as a WIN — Wen's PRIMARY still got +1.
    assert (await RoleCapability.get(
        player=seeded["players"]["Wenweia"], role="primary")
    ).success_count == wen_before + 1
    # Party 2's explicit LOSS did NOT — Minethuselah's HEALER stayed at 0.
    await mine_cap.refresh_from_db()
    assert mine_cap.success_count == 0


async def test_bucketed_players_never_earn_win_credit(seeded):
    """Unassigned / Volunteering / Sitting-out earn NOTHING, even holding an
    assigned role. Staff routinely set a role on a card before dragging it
    into a party (and leave it set when they drag it back out), so a role on
    a bucketed placement records an intent that never happened — crediting it
    would hand out wins to people who sat the anni out."""
    event = seeded["event"]
    settings = get_settings()
    grace = settings.grace_hours * 3600

    # One player per bucket, each given a core role + a matching capability.
    bucketed = {
        "Paradrex": BucketKind.UNASSIGNED,    # unassigned (main lane)
        "Sevisoup": BucketKind.VOLUNTEERS,    # volunteering
        "ThinKing": BucketKind.WONTASSIGN,    # sitting out
    }
    caps = {}
    for name, bucket in bucketed.items():
        player = seeded["players"][name]
        place = await BoardPlacement.get(event=event, player=player)
        assert place.bucket is bucket and place.party_id is None
        place.assigned_role = "tank"
        await place.save(update_fields=["assigned_role"])
        caps[name] = await RoleCapability.create(
            player=player, role="tank", confidence="high", success_count=0,
        )

    event.stamp_epoch = int(time.time()) - grace - 10  # EXPIRED
    await event.save(update_fields=["stamp_epoch"])
    wen_before = (await RoleCapability.get(
        player=seeded["players"]["Wenweia"], role="primary")).success_count

    await lifecycle_task._tick(AppState(), settings)

    for name, cap in caps.items():
        await cap.refresh_from_db()
        assert cap.success_count == 0, f"{name} was credited from a bucket"
    # ...while a real party member on the same (TBD-defaults-to-WIN) event is
    # still credited, so this asserts the exclusion and not a dead wipe.
    assert (await RoleCapability.get(
        player=seeded["players"]["Wenweia"], role="primary")
    ).success_count == wen_before + 1


# --- reliability setbacks ---------------------------------------------------

async def _expire(event) -> None:
    event.stamp_epoch = int(time.time()) - get_settings().grace_hours * 3600 - 10
    await event.save(update_fields=["stamp_epoch"])


async def _mark_seen(event, *names_players) -> None:
    for player in names_players:
        await Rsvp.filter(event=event, player=player).update(
            seen_online_at=datetime.now(timezone.utc)
        )


async def test_loss_party_members_get_a_loss_setback_in_their_role(seeded):
    """Mirrors the win credit: party members with a core role they hold a
    capability for. Minethuselah sits in the LAG party with no role."""
    event, p = seeded["event"], seeded["players"]
    await Party.filter(event=event, ordinal=1).update(result=PartyResult.LOSS)
    await Party.filter(event=event, ordinal=2).update(result=PartyResult.LAG)
    await _mark_seen(event, p["Wenweia"])  # presence tracking was running
    await _expire(event)

    await lifecycle_task._tick(AppState(), get_settings())

    losses = await Setback.filter(
        event=event, kind=SetbackKind.LOSS,
    ).prefetch_related("player")
    got = sorted((s.player.mc_username, s.role.value) for s in losses)
    assert got == sorted({
        ("Wenweia", Role.PRIMARY.value),
        ("Nazzae", Role.HEALER.value),
        ("_akaPasta", Role.TANK.value),
    })
    stamp = datetime.fromtimestamp(event.stamp_epoch, tz=timezone.utc)
    assert [s.occurred_at for s in losses] == [stamp] * 3
    # A loss is not a win.
    assert (await RoleCapability.get(
        player=p["Wenweia"], role="primary")).success_count == 12


async def test_hard_rsvp_nobody_saw_is_a_missed_setback(seeded):
    """Seeded hard RSVPs: Wenweia + Nazzae (in party 1), Metrafish (API
    hidden), foo (unassigned, API visible). Only foo is provably absent."""
    event, p = seeded["event"], seeded["players"]
    await _mark_seen(event, p["Nazzae"])
    await _expire(event)

    await lifecycle_task._tick(AppState(), get_settings())

    missed = await Setback.filter(
        event=event, kind=SetbackKind.MISSED,
    ).prefetch_related("player")
    assert [(s.player.mc_username, s.role) for s in missed] == [("foo", None)]


async def test_being_seen_in_the_hot_window_is_turning_up(seeded):
    event, p = seeded["event"], seeded["players"]
    await _mark_seen(event, p["foo"])
    await _expire(event)

    await lifecycle_task._tick(AppState(), get_settings())

    assert not await Setback.filter(kind=SetbackKind.MISSED).exists()


async def test_no_misses_when_presence_tracking_never_ran(seeded):
    """Nobody's RSVP was ever stamped seen: the process was down for the
    anni, so a no-show can't be told from a blind spot."""
    await _expire(seeded["event"])

    await lifecycle_task._tick(AppState(), get_settings())

    assert not await Setback.filter(kind=SetbackKind.MISSED).exists()


async def test_live_api_hidden_verdict_exempts_a_miss(seeded):
    event, p = seeded["event"], seeded["players"]
    await _mark_seen(event, p["Nazzae"])
    await _expire(event)
    state = AppState(api_hidden_by_uuid={p["foo"].mc_uuid: True})

    await lifecycle_task._tick(state, get_settings())

    assert not await Setback.filter(kind=SetbackKind.MISSED).exists()
