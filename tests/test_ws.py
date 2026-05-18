"""Board WebSocket: the wire protocol + the server-authoritative hub.

A fresh ``BoardHub`` per test (not the process singleton) so ``seq`` never
bleeds between tests. The "client" is a tiny fake — the hub is deliberately
FastAPI-free so this needs no real socket.
"""

from __future__ import annotations

import json
import time

from app.db.models import AnniEvent, BoardPlacement, Party
from app.services.state import AppState
from app.web.ws import protocol as P
from app.web.ws.board_hub import BoardHub


class FakeClient:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.frames.append(json.loads(data))

    def last(self) -> dict:
        return self.frames[-1]

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]


# --- protocol (pure) -------------------------------------------------------
def test_parse_intent_is_shape_strict_field_tolerant():
    i = P.parse_intent({"v": 1, "type": "MOVE", "op_id": "x",
                         "player_uuid": "u", "target": {"bucket": "unassigned"}})
    assert i is not None and i.type == P.MOVE and i.op_id == "x"
    assert i.data["player_uuid"] == "u"  # envelope keys stripped, rest kept

    assert P.parse_intent("not-a-dict") is None
    assert P.parse_intent({"type": "NOPE"}) is None
    assert P.parse_intent({"type": "PING"}).op_id is None


def test_frame_builders_carry_the_envelope():
    for frame in (
        P.welcome(3, {"x": 1}), P.applied("o", 4), P.rejected("o", "no", 4),
        P.patch(5, [{"op": "snapshot"}]), P.board_wipe(None), P.pong(),
    ):
        assert frame["v"] == P.PROTOCOL_VERSION and "type" in frame


# --- hub --------------------------------------------------------------------
async def test_hello_only_welcomes_the_asker_with_a_full_snapshot(seeded):
    hub, state = BoardHub(), AppState()
    a, b = FakeClient(), FakeClient()
    hub.register(a)
    hub.register(b)
    assert hub.client_count == 2

    await hub.handle(a, P.Intent(P.HELLO), seeded["event"], state)
    assert a.types() == [P.WELCOME] and b.frames == []
    snap = a.last()["snapshot"]
    assert {"event", "parties", "buckets", "roster"} <= snap.keys()
    # Same shape the SSR board renders from (single source).
    assert "unassigned" in snap["buckets"] and "late" in snap["buckets"]["unassigned"]


async def test_ping_pongs(seeded):
    hub = BoardHub()
    c = FakeClient()
    await hub.handle(c, P.Intent(P.PING), seeded["event"], AppState())
    assert c.types() == [P.PONG]


async def test_move_applies_acks_actor_and_broadcasts_snapshot(seeded):
    hub, state = BoardHub(), AppState()
    actor, other = FakeClient(), FakeClient()
    hub.register(actor)
    hub.register(other)
    wen = seeded["players"]["Wenweia"]

    await hub.handle(
        actor,
        P.Intent(P.MOVE, op_id="op1",
                 data={"player_uuid": wen.mc_uuid,
                       "target": {"bucket": "wontassign"}}),
        seeded["event"], state,
    )
    # Actor: APPLIED(op1) + the reconciling snapshot PATCH. Other: just PATCH.
    assert P.APPLIED in actor.types() and P.PATCH in actor.types()
    applied = next(f for f in actor.frames if f["type"] == P.APPLIED)
    assert applied["op_id"] == "op1"
    assert other.types() == [P.PATCH]
    # Both PATCHes share the one authoritative seq -> tabs converge.
    assert actor.frames[-1]["seq"] == other.frames[-1]["seq"] == applied["seq"]
    moved = await BoardPlacement.get(event=seeded["event"], player=wen)
    assert moved.bucket.value == "wontassign"


async def test_invalid_move_rejects_actor_only_no_broadcast(seeded):
    hub = BoardHub()
    actor, other = FakeClient(), FakeClient()
    hub.register(actor)
    hub.register(other)

    await hub.handle(
        actor,
        P.Intent(P.MOVE, op_id="bad",
                 data={"player_uuid": "ghost", "target": {"bucket": "unassigned"}}),
        seeded["event"], AppState(),
    )
    assert actor.types() == [P.REJECTED]
    assert actor.last()["op_id"] == "bad" and actor.last()["reason"]
    assert other.frames == []  # a rejected op never reconciles others


async def test_seq_is_monotonic_across_ops(seeded):
    hub, state = BoardHub(), AppState()
    c = FakeClient()
    hub.register(c)
    seqs = []
    for uuid in ("Faulischlumpf", "Metrafish", "Paradrex"):
        await hub.handle(
            c,
            P.Intent(P.MOVE, data={
                "player_uuid": seeded["players"][uuid].mc_uuid,
                "target": {"bucket": "volunteers"}}),
            seeded["event"], state,
        )
    seqs = [f["seq"] for f in c.frames if f["type"] == P.PATCH]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_player_add_via_ws_is_idempotent(seeded):
    hub = BoardHub()
    state = AppState(roster_by_uuid={"uuid-zz": "Zeezee"})
    c = FakeClient()
    hub.register(c)
    event = seeded["event"]
    n0 = await BoardPlacement.filter(event=event).count()

    for _ in range(3):  # add the same walk-in three times
        await hub.handle(c, P.Intent(P.PLAYER_ADD, data={"ign": "Zeezee"}),
                         event, state)
    assert await BoardPlacement.filter(event=event).count() == n0 + 1
    assert c.types().count(P.APPLIED) == 3  # each call acks (no-op is still ok)


async def test_unknown_ign_player_add_rejects(seeded):
    hub = BoardHub()
    c = FakeClient()
    hub.register(c)
    await hub.handle(c, P.Intent(P.PLAYER_ADD, op_id="pa", data={"ign": "ghost"}),
                     seeded["event"], AppState())
    assert c.last()["type"] == P.REJECTED and "Couldn't find" in c.last()["reason"]


async def test_grace_freezes_board_except_party_result_stage(db):
    """now>stamp & ≤stamp+grace ⇒ only PARTY_SET(result/stage) is allowed."""
    hub, state = BoardHub(), AppState()
    past = int(time.time()) - 60  # just started -> GRACE
    event = await AnniEvent.create(stamp_epoch=past, is_active=True)
    party = await Party.create(event=event, ordinal=1)
    c = FakeClient()
    hub.register(c)

    await hub.handle(c, P.Intent(P.PARTY_CREATE, op_id="pc"), event, state)
    assert c.last()["type"] == P.REJECTED and "read-only" in c.last()["reason"]

    await hub.handle(
        c,
        P.Intent(P.PARTY_SET, op_id="ps",
                 data={"party_id": str(party.id), "result": "win", "stage": 5}),
        event, state,
    )
    assert P.APPLIED in c.types()
    await party.refresh_from_db()
    assert party.result.value == "win" and party.stage == 5
