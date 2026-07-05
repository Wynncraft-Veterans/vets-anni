"""Autoparty seeder — the "Create Initial Autoparties (experimental)" flow.

Covers the algorithm's rule invariants (rule 6 = every party has ≥1 per core
role; rule 3 = doubling priority Healer > Tertiary > Primary > Secondary >
Tank; rule 5 = experience spread), the K-derivation math (two softs equal one
strong for coverage), and the intent/REST wiring (dispatch, broadcast, grace
freeze). Custom scenarios are built directly against the ``db`` fixture so we
control the pool composition precisely; a couple of tests still lean on the
``seeded`` dataset to sanity-check integration with the real dev dataset."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest_asyncio

from app.constants import (
    AttendanceNotice,
    BucketKind,
    ConfidenceLevel,
    PARTY_CAPACITY,
    PresenceStatus,
    Role,
)
from app.db.models import (
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    Rsvp,
)
from app.domain import autoparty, buckets
from app.services.state import AppState
from app.web import deps
from app.web.ws import protocol as P
from app.web.ws.board_hub import BoardHub


# --- helpers ---------------------------------------------------------------
class FakeClient:
    """Tiny stand-in for a websocket (mirrors tests/test_ws.py)."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.frames.append(json.loads(data))

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]

    def last(self) -> dict:
        return self.frames[-1]


async def _make_event(*, future: bool = True) -> AnniEvent:
    now = datetime.now(UTC)
    stamp = int((now + timedelta(minutes=60 if future else -60)).timestamp())
    return await AnniEvent.create(stamp_epoch=stamp, is_active=True)


async def _add_player(
    event: AnniEvent,
    name: str,
    *,
    bucket: BucketKind = BucketKind.UNASSIGNED,
    caps: list[tuple[Role, ConfidenceLevel, int]] | None = None,
    rsvp: AttendanceNotice | None = None,
) -> AnniPlayer:
    """Create a player + capabilities + optional RSVP + a placement in
    ``bucket``. UUID is derived from name so it's stable."""
    uuid = f"uuid-{name.lower()}"
    p = await AnniPlayer.create(
        mc_uuid=uuid, mc_username=name, wynn_username=name
    )
    for role, conf, wins in caps or []:
        await RoleCapability.create(
            player=p, role=role, confidence=conf,
            build_quality=ConfidenceLevel.MODERATE, success_count=wins,
        )
    await BoardPlacement.create(
        event=event, player=p, bucket=bucket, sort_index=0,
    )
    if rsvp is not None:
        await Rsvp.create(event=event, player=p, notice=rsvp)
    return p


def _online(*names: str) -> dict[str, str]:
    """Build a ``presence_by_uuid`` map that puts the named players
    ``ONLINE_ELSEWHERE`` (any of the ONLINE_* states counts as strong)."""
    return {f"uuid-{n.lower()}": PresenceStatus.ONLINE_ELSEWHERE.value
            for n in names}


CORE = [Role.PRIMARY, Role.SECONDARY, Role.TERTIARY, Role.HEALER, Role.TANK]


async def _seed_five_strong_hards(event: AnniEvent) -> list[AnniPlayer]:
    """One HARD-RSVP'd player per core role, HIGH confidence, no wins.
    The minimum-viable pool for K=1 with clean coverage."""
    return [
        await _add_player(
            event, f"P{i}Hard",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
        for i, role in enumerate(CORE)
    ]


# --- rejection paths -------------------------------------------------------
async def test_seed_rejects_when_parties_exist(db):
    event = await _make_event()
    await Party.create(event=event, ordinal=1)
    await _seed_five_strong_hards(event)

    r = await autoparty.seed_initial(event, AppState())
    assert not r.ok and "zero parties" in r.reason


async def test_seed_rejects_when_pool_empty(db):
    event = await _make_event()
    r = await autoparty.seed_initial(event, AppState())
    assert not r.ok and "No candidates" in r.reason


async def test_seed_rejects_when_coverage_insufficient(db):
    """No tank in the whole pool → K=0 → reject with a friendly reason."""
    event = await _make_event()
    # 5 hard players but zero of them can tank.
    for i, role in enumerate([Role.PRIMARY, Role.SECONDARY, Role.TERTIARY,
                              Role.HEALER, Role.PRIMARY]):
        await _add_player(
            event, f"NoTank{i}",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
    r = await autoparty.seed_initial(event, AppState())
    assert not r.ok and "role coverage" in r.reason


# --- happy paths -----------------------------------------------------------
async def test_seed_five_hards_creates_one_party_with_full_core_coverage(db):
    event = await _make_event()
    await _seed_five_strong_hards(event)

    r = await autoparty.seed_initial(event, AppState())
    assert r.ok
    parties = await Party.filter(event=event)
    assert len(parties) == 1

    placements = await BoardPlacement.filter(event=event, party=parties[0])
    assigned = {p.assigned_role for p in placements}
    assert set(CORE) <= assigned  # rule 6 for the single party


async def test_seed_hard_rule_all_parties_have_every_core_role(db):
    """Rule 6 invariant across K=2 parties: each party gets ≥1 per core role."""
    event = await _make_event()
    # 10 HARD players, 2 of each core role.
    for i in range(2):
        for role in CORE:
            await _add_player(
                event, f"{role.value}{i}",
                caps=[(role, ConfidenceLevel.HIGH, 0)],
                rsvp=AttendanceNotice.RSVP_HARD,
            )

    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    parties = await Party.filter(event=event).order_by("ordinal")
    assert len(parties) == 2

    for party in parties:
        placements = await (
            BoardPlacement.filter(event=event, party=party)
            .select_related("player")
        )
        assigned = {p.assigned_role for p in placements}
        assert set(CORE) <= assigned, (
            f"Party {party.ordinal} missing roles: "
            f"{set(CORE) - assigned}"
        )


async def test_seed_K_is_capped_by_scarcest_role(db):
    """Enough for 3 parties on every role except tank; 1 tank → K=1."""
    event = await _make_event()
    for role in [Role.PRIMARY, Role.SECONDARY, Role.TERTIARY, Role.HEALER]:
        for i in range(15):  # 15 of each = plenty
            await _add_player(
                event, f"{role.value}{i}",
                caps=[(role, ConfidenceLevel.HIGH, 0)],
                rsvp=AttendanceNotice.RSVP_HARD,
            )
    await _add_player(
        event, "OnlyTank",
        caps=[(Role.TANK, ConfidenceLevel.HIGH, 0)],
        rsvp=AttendanceNotice.RSVP_HARD,
    )
    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    assert await Party.filter(event=event).count() == 1


async def test_seed_prefers_strong_over_two_softs(db):
    """When a role has 1 hard + 2 softs, the hard takes the seat (rule 1)."""
    event = await _make_event()
    # 4 hard roles + Healer = 1 hard + 2 softs on it.
    for role in [Role.PRIMARY, Role.SECONDARY, Role.TERTIARY, Role.TANK]:
        await _add_player(
            event, f"H{role.value}",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
    hard_healer = await _add_player(
        event, "HardHealer",
        caps=[(Role.HEALER, ConfidenceLevel.HIGH, 0)],
        rsvp=AttendanceNotice.RSVP_HARD,
    )
    for i in range(2):
        await _add_player(
            event, f"SoftHealer{i}",
            caps=[(Role.HEALER, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_SOFT,
        )

    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    party = await Party.filter(event=event).first()
    healers = await (
        BoardPlacement.filter(event=event, party=party,
                              assigned_role=Role.HEALER)
        .select_related("player")
    )
    healer_names = {p.player.mc_username for p in healers}
    # Hard healer is the primary role holder; softs may double via pass 2
    # (they're capable + strong-not-available, so they land as extras).
    assert "HardHealer" in healer_names
    hh_placement = next(p for p in healers if p.player == hard_healer)
    assert hh_placement.assigned_role == Role.HEALER


async def test_seed_two_softs_fallback_when_no_strong(db):
    """A role with 0 strong + 2 softs → both softs get assigned_role=role."""
    event = await _make_event()
    for role in [Role.PRIMARY, Role.SECONDARY, Role.TERTIARY, Role.TANK]:
        await _add_player(
            event, f"H{role.value}",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
    for i in range(2):
        await _add_player(
            event, f"SoftH{i}",
            caps=[(Role.HEALER, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_SOFT,
        )

    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    party = await Party.filter(event=event).first()
    healers = await BoardPlacement.filter(
        event=event, party=party, assigned_role=Role.HEALER,
    )
    # Both softs land on the healer slot (rule 2 as fallback quality bar).
    assert len(healers) == 2


async def test_seed_online_counts_as_strong(db):
    """A no-RSVP but online-elsewhere player counts as strong for coverage."""
    event = await _make_event()
    for role in [Role.PRIMARY, Role.SECONDARY, Role.TERTIARY, Role.HEALER]:
        await _add_player(
            event, f"H{role.value}",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
    tank = await _add_player(
        event, "OnlineTank",
        caps=[(Role.TANK, ConfidenceLevel.HIGH, 0)],
        # No RSVP — coverage would fail without the online signal.
    )
    state = AppState(presence_by_uuid=_online("OnlineTank"))

    r = await autoparty.seed_initial(event, state)
    assert r.ok, r.reason
    placed = await BoardPlacement.get(event=event, player=tank)
    assert placed.assigned_role == Role.TANK


async def test_seed_doubling_priority_healer_first(db):
    """Excess capacity → HEALER doubles before TERTIARY before … before TANK."""
    event = await _make_event()
    # Core coverage (5 people, 1 per role, all hard).
    for role in CORE:
        await _add_player(
            event, f"Core{role.value}",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
    # Extras: multiple hards capable of each role, so the algorithm picks who
    # to double first purely by role priority.
    for role in CORE:
        for i in range(3):
            await _add_player(
                event, f"Extra{role.value}{i}",
                caps=[(role, ConfidenceLevel.HIGH, 0)],
                rsvp=AttendanceNotice.RSVP_HARD,
            )
    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    party = await Party.filter(event=event).first()
    placements = await BoardPlacement.filter(event=event, party=party)
    # Count how many of each role landed. Priority: HEALER > TERTIARY >
    # PRIMARY > SECONDARY > TANK, so healer count >= tertiary >= primary >=
    # secondary >= tank (all >=1 by rule 6).
    counts = {role: 0 for role in CORE}
    for p in placements:
        if p.assigned_role in counts:
            counts[p.assigned_role] += 1
    assert counts[Role.HEALER] >= counts[Role.TERTIARY]
    assert counts[Role.TERTIARY] >= counts[Role.PRIMARY]
    assert counts[Role.PRIMARY] >= counts[Role.SECONDARY]
    assert counts[Role.SECONDARY] >= counts[Role.TANK]
    assert counts[Role.TANK] >= 1  # rule 6 floor


async def test_seed_experience_is_spread(db):
    """K=2, each role has 1 exp-heavy + 1 fresh candidate → the algorithm
    spreads them so each party gets some experience (rule 5)."""
    event = await _make_event()
    # 10 hard players, 2 per role, one exp-heavy (12 wins) and one fresh.
    # K_coverage = min(strong=2 per role) = 2. K_capacity = 10 // 5 = 2. K=2.
    for role in CORE:
        await _add_player(
            event, f"{role.value}E",
            caps=[(role, ConfidenceLevel.HIGH, 12)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
        await _add_player(
            event, f"{role.value}N",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    parties = await Party.filter(event=event).order_by("ordinal")
    assert len(parties) == 2

    per_party_exp: list[int] = []
    for p in parties:
        rows = await (
            BoardPlacement.filter(event=event, party=p)
            .prefetch_related("player__capabilities")
        )
        exp = sum(
            c.success_count
            for row in rows
            for c in row.player.capabilities
        )
        per_party_exp.append(exp)
    # Both parties get experience — neither is starved (rule 5 spread).
    assert min(per_party_exp) > 0
    # With 5 exp-heavy candidates (12 wins each) and K=2, a perfect split
    # would be 36/24 (roughly). Allow some algorithmic wiggle room.
    assert max(per_party_exp) - min(per_party_exp) <= 24


async def test_seed_pulls_from_volunteers_bucket(db):
    """A candidate in VOLUNTEERS is eligible + drawn if needed for coverage."""
    event = await _make_event()
    for role in [Role.PRIMARY, Role.SECONDARY, Role.TERTIARY, Role.HEALER]:
        await _add_player(
            event, f"H{role.value}",
            caps=[(role, ConfidenceLevel.HIGH, 0)],
            rsvp=AttendanceNotice.RSVP_HARD,
        )
    volunteer_tank = await _add_player(
        event, "VolTank",
        bucket=BucketKind.VOLUNTEERS,
        caps=[(Role.TANK, ConfidenceLevel.HIGH, 0)],
        rsvp=AttendanceNotice.RSVP_HARD,
    )
    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    placement = await BoardPlacement.get(event=event, player=volunteer_tank)
    assert placement.party is not None  # pulled out of VOLUNTEERS
    assert placement.assigned_role == Role.TANK


async def test_seed_overflow_stays_in_origin_bucket(db):
    """K=1 with 12 candidates → 10 placed, 2 remain in Unassigned unchanged."""
    event = await _make_event()
    # 12 HARD players, 2+ per role, but K=1 (12/5 = 2 by capacity, but
    # 2 of each role → covered=2 too → K=2). To force K=1, give each role
    # exactly 2 candidates → K = min(2, 12//5) = min(2, 2) = 2. So use 3 of
    # some roles and skew to enforce K=1: put just 1 tank in the pool.
    for role in [Role.PRIMARY, Role.SECONDARY, Role.TERTIARY, Role.HEALER]:
        for i in range(3):
            await _add_player(
                event, f"{role.value}{i}",
                caps=[(role, ConfidenceLevel.HIGH, 0)],
                rsvp=AttendanceNotice.RSVP_HARD,
            )
    await _add_player(
        event, "SoloTank",
        caps=[(Role.TANK, ConfidenceLevel.HIGH, 0)],
        rsvp=AttendanceNotice.RSVP_HARD,
    )
    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    parties = await Party.filter(event=event)
    assert len(parties) == 1

    party_rows = await BoardPlacement.filter(event=event, party=parties[0])
    assert len(party_rows) == PARTY_CAPACITY  # rule 7: filled to capacity

    leftover = await BoardPlacement.filter(
        event=event, bucket=BucketKind.UNASSIGNED, party=None,
    )
    # 13 candidates - 10 placed = 3 leftover in origin bucket.
    assert len(leftover) == 3


async def test_seed_capability_less_candidates_can_serve_as_fill(db):
    """A no-caps candidate isn't picked for a core seat but can pad FILL to
    avoid leaving them unassigned (rule 7)."""
    event = await _make_event()
    # Full core coverage from 5 hards.
    await _seed_five_strong_hards(event)
    # Six extras with NO capabilities → FILL padding candidates.
    filler_names = []
    for i in range(6):
        name = f"NoCap{i}"
        filler_names.append(name)
        await _add_player(
            event, name, rsvp=AttendanceNotice.RSVP_HARD,
        )

    r = await autoparty.seed_initial(event, AppState())
    assert r.ok, r.reason
    party = await Party.filter(event=event).first()
    party_rows = await (
        BoardPlacement.filter(event=event, party=party)
        .select_related("player")
    )
    fill_placements = [
        r for r in party_rows if r.assigned_role == Role.FILL
    ]
    # 5 core seats + up to 5 fill = full party at PARTY_CAPACITY.
    assert len(party_rows) == PARTY_CAPACITY
    assert len(fill_placements) == 5


# --- WS + REST integration -------------------------------------------------
async def test_seed_ws_intent_dispatches_and_broadcasts(db):
    event = await _make_event()
    await _seed_five_strong_hards(event)
    hub = BoardHub()
    actor, other = FakeClient(), FakeClient()
    hub.register(actor)
    hub.register(other)

    await hub.handle(
        actor, P.Intent(P.AUTOPARTIES_SEED, op_id="seed"),
        event, AppState(),
    )
    assert P.APPLIED in actor.types() and P.PATCH in actor.types()
    applied = next(f for f in actor.frames if f["type"] == P.APPLIED)
    assert applied["op_id"] == "seed"
    # Passive tab receives the reconciling snapshot too.
    assert other.types() == [P.PATCH]
    # DB actually changed.
    assert await Party.filter(event=event).count() == 1


async def test_seed_ws_intent_rejects_during_grace(db):
    event = await _make_event(future=False)  # started already → GRACE
    await _seed_five_strong_hards(event)
    hub = BoardHub()
    c = FakeClient()
    hub.register(c)

    await hub.handle(
        c, P.Intent(P.AUTOPARTIES_SEED, op_id="seed"),
        event, AppState(),
    )
    assert c.last()["type"] == P.REJECTED and "read-only" in c.last()["reason"]
    assert await Party.filter(event=event).count() == 0


async def test_seed_ws_intent_rejects_when_parties_exist(db):
    event = await _make_event()
    await Party.create(event=event, ordinal=1)
    await _seed_five_strong_hards(event)
    hub = BoardHub()
    c = FakeClient()
    hub.register(c)

    await hub.handle(
        c, P.Intent(P.AUTOPARTIES_SEED, op_id="seed"),
        event, AppState(),
    )
    assert c.last()["type"] == P.REJECTED
    assert "zero parties" in c.last()["reason"]


# --- REST twin --------------------------------------------------------------
@pytest_asyncio.fixture
async def as_staff(client):
    """A client with the staff-session cookie (mirror test_board_smoke)."""
    client.cookies.set(
        "anni_session", deps._serializer.dumps({"kind": "staff"})
    )
    return client


async def test_rest_twin_seeds_via_board_hub(as_staff, db):
    event = await _make_event()
    await _seed_five_strong_hards(event)

    r = await as_staff.post("/staff/board/autoparties")
    assert r.status_code == 200
    assert 'id="board"' in r.text
    assert await Party.filter(event=event).count() == 1


async def test_rest_twin_rejects_when_parties_exist(as_staff, db):
    event = await _make_event()
    await Party.create(event=event, ordinal=1)
    await _seed_five_strong_hards(event)

    r = await as_staff.post("/staff/board/autoparties")
    assert r.status_code == 200
    # Friendly inline reject rendered as a danger bar (mirrors the pattern
    # test_rest_player_add_unknown_ign_shows_friendly_error uses).
    assert "zero parties" in r.text
    assert 'class="bar bar-danger"' in r.text


async def test_rest_twin_requires_staff(client, db):
    r = await client.post("/staff/board/autoparties", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/staff"


# --- template button gating ------------------------------------------------
async def test_button_visible_only_when_zero_parties(as_staff, db):
    """The button appears when no parties exist and hides once one is added."""
    event = await _make_event()
    await _seed_five_strong_hards(event)

    r = await as_staff.get("/staff/board")
    assert r.status_code == 200
    assert "Create Initial Autoparties" in r.text

    # Add a party manually → button should disappear on next render.
    await buckets.create_party(event)
    r = await as_staff.get("/staff/board")
    assert "Create Initial Autoparties" not in r.text
