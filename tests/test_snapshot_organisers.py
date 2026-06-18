"""S7 snapshot fields — ``organisers`` + ``organiser_usernames`` parity.

The S7 gate runs on usernames (vetsmod has no reliable name→UUID resolution),
so both projections must be emitted in the same parallel order with no
duplicates by UUID.
"""

from __future__ import annotations

from app.db.lifecycle import get_active_event
from app.domain.snapshot import assemble_snapshot
from app.services.state import AppState


async def test_organisers_and_usernames_parallel(seeded):
    """Both lists are emitted in the same order (lead first, then party hosts
    in ordinal order). Seed: organizer = Holidaze, Party 1 host = Holidaze
    (dedup → one entry), Party 2 host = Nazzae."""
    event = await get_active_event()
    snap = await assemble_snapshot(
        seeded["players"]["Wenweia"], event, AppState()
    )

    uuids = snap["organisers"]
    names = snap["organiser_usernames"]
    assert len(uuids) == len(names)
    # Holidaze (lead + party 1 host, deduped) then Nazzae (party 2 host).
    assert names == ["Holidaze", "Nazzae"]
    assert uuids == [
        seeded["players"]["Holidaze"].mc_uuid,
        seeded["players"]["Nazzae"].mc_uuid,
    ]


async def test_organisers_empty_when_no_event(seeded, monkeypatch):
    """When the snapshot path has no active event (organisers branch skipped),
    both lists are empty."""
    # Force the "no active event" branch by passing event=None to
    # ``assemble_snapshot`` directly.
    snap = await assemble_snapshot(
        seeded["players"]["Wenweia"], None, AppState()
    )
    assert snap["organisers"] == []
    assert snap["organiser_usernames"] == []
