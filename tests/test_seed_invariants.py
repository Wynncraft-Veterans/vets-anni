"""The dev dataset exists specifically to demonstrate the domain invariants.

So assert them directly against the seeded rows. If one of these breaks, the
"seed dev data" launch config is producing a misleading picture and a real
hard-rule (CLAUDE.md) has regressed.
"""

from __future__ import annotations

import pytest
from tortoise.exceptions import IntegrityError

import seed_dev  # scripts/seed_dev.py, via conftest sys.path
from app.constants import API_DISABLED_LAST_ONLINE_MAX, BucketKind
from app.db.lifecycle import ensure_single_active, get_active_event
from app.db.models import (
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    RoleCapabilityWeapon,
    Rsvp,
)


async def test_row_counts_match_the_seed(seeded):
    assert await AnniPlayer.all().count() == len(seed_dev.PLAYERS) == 16
    assert await Party.all().count() == 2
    assert await BoardPlacement.all().count() == 15
    assert await Rsvp.all().count() == 6
    assert await RoleCapability.all().count() == 5
    assert await RoleCapabilityWeapon.all().count() == 6


async def test_exactly_one_active_event_eager_loads_organizer(seeded):
    assert await AnniEvent.filter(is_active=True).count() == 1
    event = await get_active_event()
    assert event is not None
    # organizer is select_related'd: readable without an awaited relation
    # access (Jinja can't await — a lazy FK here would raise).
    assert event.organizer.mc_username == "Holidaze"


async def test_single_instance_per_person_is_enforced(seeded):
    """BoardPlacement.unique_together(event, player): a person can never be
    duplicated across buckets/parties (CLAUDE.md hard rule)."""
    event = seeded["event"]
    already_placed = seeded["players"]["Wenweia"]
    with pytest.raises(IntegrityError):
        await BoardPlacement.create(
            event=event, player=already_placed, bucket=BucketKind.UNASSIGNED
        )


async def test_api_disabled_player_uses_the_epoch_sentinel(seeded):
    """Metrafish has the API disabled: last_online == unix epoch, which the
    documented heuristic (<= epoch + API_DISABLED_LAST_ONLINE_MAX) catches."""
    metra = await AnniPlayer.get(mc_username="Metrafish")
    assert metra.last_online == seed_dev.EPOCH
    assert metra.last_online.timestamp() <= API_DISABLED_LAST_ONLINE_MAX


async def test_rename_desync_is_representable(seeded):
    """_akaPasta's in-game (wynn) name differs from the resolved mc name;
    everyone else's matches (seed sets ``wynn_username = wynn or name``)."""
    pasta = await AnniPlayer.get(mc_username="_akaPasta")
    assert pasta.wynn_username == "ISnortPasta"
    assert pasta.wynn_username != pasta.mc_username

    normal = await AnniPlayer.get(mc_username="Wenweia")
    assert normal.wynn_username == normal.mc_username


async def test_ensure_single_active_demotes_the_old_event(seeded):
    """Rotating in a new event must leave exactly one active row."""
    old = await get_active_event()
    new = await AnniEvent.create(stamp_epoch=old.stamp_epoch + 60, is_active=True)

    await ensure_single_active(new)

    assert await AnniEvent.filter(is_active=True).count() == 1
    current = await get_active_event()
    assert current.id == new.id
