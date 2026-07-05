"""Autoparty seeder — the "Create Initial Autoparties (experimental)" flow.

One entry point: :func:`seed_initial`. Composes ``domain/buckets`` primitives
(``create_party`` / ``move`` / ``assign_role``) so every write funnels through
the single-instance UPSERT — the board invariant (``.claude/data_model.md``)
still holds even when we create N parties + N × 10 placements in one shot.

The rules — from ``.claude/ephemeral/auto-mode.md`` and the session Q&A:

1. Each party needs ≥1 **strong** (HARD-RSVP or online) member per core role.
2. If a role has no strong candidate, two soft-RSVP members are the fallback
   (both placed with ``assigned_role=r`` — honest about what the algorithm did;
   the organiser sees "2 healers" and reshuffles by hand if both actually show).
3. When capacity allows, double up by priority:
   HEALER > TERTIARY > PRIMARY > SECONDARY > TANK.
4. Prefer players on the roles they're most comfortable with (``confidence`` +
   ``success_count``).
5. Spread experienced players across parties.
6. Hard floor: every party has at least one person per core role (guaranteed
   by construction — K is capped by the scarcest role's coverable count).
7. Prefer not to leave people unassigned.
8. Overflow bias: least-likely-to-attend stay unassigned (satisfied for free
   because the algorithm processes the strong pool first).

Candidates are drawn from **Unassigned (all lanes) + Volunteers**. Overflow
(unplaced) candidates stay in their origin bucket, untouched — the seed only
*creates* parties + moves the candidates it decides to place.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from tortoise.transactions import in_transaction

from app.constants import (
    CAPABILITY_ROLES,
    PARTY_CAPACITY,
    AttendanceNotice,
    BucketKind,
    ConfidenceLevel,
    PresenceStatus,
    Role,
)
from app.db.models import AnniEvent, Party, Rsvp
from app.domain import buckets
from app.domain.buckets import OpResult
from app.services.state import AppState

logger = logging.getLogger("anni.autoparty")

#: Presence statuses that count as "online" for the strong-coverage predicate.
#: Queued players are ONLINE_ELSEWHERE (``.claude/domain_rules.md``) — they are
#: connecting, so we treat them as strong just like a player in the world.
_ONLINE_STATES: frozenset[str] = frozenset(
    {
        PresenceStatus.ONLINE_PARTY.value,
        PresenceStatus.ONLINE_WORLD.value,
        PresenceStatus.ONLINE_ELSEWHERE.value,
    }
)

#: Doubling priority (rule 3): HEALER > TERTIARY > PRIMARY > SECONDARY > TANK.
#: Extra healers are the most valuable; extra tanks essentially useless.
_DOUBLE_PRIORITY: tuple[Role, ...] = (
    Role.HEALER,
    Role.TERTIARY,
    Role.PRIMARY,
    Role.SECONDARY,
    Role.TANK,
)

#: Higher = better fit for a role slot; drives the primary sort in candidate
#: selection (rule 4 — comfort).
_CONFIDENCE_RANK: dict[ConfidenceLevel, int] = {
    ConfidenceLevel.HIGH: 3,
    ConfidenceLevel.MODERATE: 2,
    ConfidenceLevel.LOW: 1,
}


@dataclass
class _Candidate:
    """One eligible player + the signals the seeder uses to place them."""

    uuid: str
    caps: dict[Role, "object"]           # role -> RoleCapability (has .confidence, .success_count)
    strong_roles: frozenset[Role]        # roles for which this candidate is HARD-or-online
    soft_roles: frozenset[Role]          # roles for which this candidate is SOFT-only
    exp_score: int                       # sum of success_count across capabilities


@dataclass
class _Assignment:
    """One planned placement: which party, which role (may be None for a FILL
    padding candidate)."""

    uuid: str
    party_idx: int
    role: Role | None
    sort_index: int


@dataclass
class _Plan:
    """The full seed plan: K parties + the per-party assignment list."""

    K: int
    assignments: list[_Assignment] = field(default_factory=list)


# --- public entry point ----------------------------------------------------
async def seed_initial(event: AnniEvent, state: AppState) -> OpResult:
    """Seed initial parties from Unassigned + Volunteers.

    Guard-first (parties already exist / empty pool / uncoverable) → friendly
    ``OpResult(False, reason)``. On success, creates K parties and places the
    plan in one transaction — a mid-flight coverage failure rolls back cleanly
    (no half-created parties on the board)."""
    if await Party.filter(event=event).exists():
        return OpResult(
            False,
            "Autoparties only work when zero parties exist. "
            "Delete existing empty parties first.",
        )

    cands = await _collect_candidates(event, state)
    if not cands:
        return OpResult(
            False, "No candidates to seed parties from — add players first."
        )

    K_max = _derive_K(cands)
    if K_max < 1:
        return OpResult(
            False,
            "Not enough role coverage: even one party can't be staffed "
            "with at least one member per core role.",
        )

    # K derivation is optimistic — a multi-role candidate is counted toward
    # every role they can play, but only actually fills one slot. If Pass 1
    # exhausts for the ambitious K, retry with K-1, K-2, …, down to 1. Rule 6
    # says "never a party without ≥1 per core role"; the user's clarification
    # was "if we can't staff K parties, staffing fewer is preferable to
    # rejecting outright — leaving folks unassigned is permissible".
    plan: _Plan | None = None
    K = K_max
    while K >= 1:
        plan = _assign(cands, K)
        if plan is not None:
            break
        K -= 1
    if plan is None:
        return OpResult(
            False,
            "Ran out of role coverage while seeding, even at K=1. "
            "Try adding players or seed by hand.",
        )

    async with in_transaction():
        parties = [await buckets.create_party(event) for _ in range(K)]
        for a in plan.assignments:
            party_id = str(parties[a.party_idx].id)
            move_res = await buckets.move(
                event, a.uuid, party_id=party_id, sort_index=a.sort_index
            )
            if not move_res.ok:
                # Should be impossible — every candidate uuid came from
                # board_rows(event). If it happens, roll back.
                logger.warning(
                    "autoparty move failed unexpectedly: %s -> %s: %s",
                    a.uuid, party_id, move_res.reason,
                )
                return OpResult(
                    False, f"Placement failed: {move_res.reason}"
                )
            if a.role is not None:
                role_res = await buckets.assign_role(event, a.uuid, a.role)
                if not role_res.ok:
                    logger.warning(
                        "autoparty assign_role failed: %s -> %s: %s",
                        a.uuid, a.role, role_res.reason,
                    )
                    return OpResult(
                        False, f"Role assignment failed: {role_res.reason}"
                    )
    logger.info(
        "autoparty seeded %d parties, %d placements (from %d candidates)",
        plan.K, len(plan.assignments), len(cands),
    )
    return OpResult(True)


# --- candidate collection --------------------------------------------------
async def _collect_candidates(
    event: AnniEvent, state: AppState
) -> list[_Candidate]:
    """Pull every Unassigned + Volunteers placement and score it.

    ``board_rows`` prefetches ``capabilities.weapons`` so this is one query. A
    candidate's ``strong_roles`` = their capable roles when they're HARD-RSVP'd
    OR presence-online; ``soft_roles`` = their capable roles when only SOFT-
    RSVP'd (and not already strong)."""
    rows = await buckets.board_rows(event)
    rsvps = {
        r.player_id: r.notice
        for r in await Rsvp.filter(event=event, revoked_at=None).only(
            "player_id", "notice"
        )
    }
    pres = state.presence_by_uuid

    cands: list[_Candidate] = []
    for row in rows:
        # Only Unassigned (any lane) and Volunteers are eligible — Wontassign
        # opted out, and anything already in a party is a no-op since we
        # guarded on "no parties exist" above.
        bucket = row.get("bucket")
        if bucket not in (BucketKind.UNASSIGNED.value, BucketKind.VOLUNTEERS.value):
            continue

        uuid = row["uuid"]
        caps_list = row.get("capabilities") or []
        caps = {c.role: c for c in caps_list if c.role in CAPABILITY_ROLES}

        notice = rsvps.get(uuid)
        is_hard = notice == AttendanceNotice.RSVP_HARD
        is_online = pres.get(uuid) in _ONLINE_STATES
        is_strong = is_hard or is_online
        is_soft = notice == AttendanceNotice.RSVP_SOFT and not is_strong

        capable = frozenset(caps.keys())
        strong_roles = capable if is_strong else frozenset()
        soft_roles = capable if is_soft else frozenset()

        exp_score = sum(c.success_count for c in caps.values())

        cands.append(_Candidate(
            uuid=uuid,
            caps=caps,
            strong_roles=strong_roles,
            soft_roles=soft_roles,
            exp_score=exp_score,
        ))
    return cands


# --- K derivation ----------------------------------------------------------
def _derive_K(cands: list[_Candidate]) -> int:
    """Max K such that each of K parties can have at least one covered slot
    per core role. Rule 2 folded into the math: two softs equal one strong for
    *coverage* purposes."""
    strong_count: Counter[Role] = Counter()
    soft_count: Counter[Role] = Counter()
    for c in cands:
        strong_count.update(c.strong_roles)
        soft_count.update(c.soft_roles)

    covered = {
        r: strong_count[r] + soft_count[r] // 2 for r in CAPABILITY_ROLES
    }
    K_coverage = min(covered.values()) if covered else 0
    # Party is 5 core roles at minimum; can't run a party with <5 candidates.
    K_capacity = max(0, len(cands) // 5)
    return min(K_coverage, K_capacity)


# --- Assignment (Passes 1 + 2) ---------------------------------------------
def _selection_key(
    c: _Candidate, role: Role, party_exp_sum: int
) -> tuple:
    """Sort key for picking the best candidate for (role, party).

    All keys negated so ascending sort picks the best. Rule ordering:
    - confidence for this role (rule 4)
    - success_count in this role (rule 4 tiebreak)
    - low exp_score first (rule 5: hold the highest-exp for later parties)
    - low party_exp_sum first (rule 5: prefer party currently lightest)"""
    cap = c.caps.get(role)
    if cap is None:
        # Should not happen — callers filter to capable candidates. Fall
        # through with a maximally bad key so an accidentally-slipped-in
        # candidate sorts last.
        return (0, 0, 0, 0)
    return (
        -_CONFIDENCE_RANK.get(cap.confidence, 0),
        -cap.success_count,
        c.exp_score,
        party_exp_sum,
    )


def _pick_best(
    pool: list[_Candidate],
    role: Role,
    party_exp_sum: int,
    *,
    filter_pred,
) -> _Candidate | None:
    """Best candidate in ``pool`` for whom ``filter_pred(c)`` is True."""
    eligible = [c for c in pool if filter_pred(c)]
    if not eligible:
        return None
    eligible.sort(key=lambda c: _selection_key(c, role, party_exp_sum))
    return eligible[0]


def _assign(cands: list[_Candidate], K: int) -> _Plan | None:
    """Two-pass assignment. Returns ``None`` if Pass 1 exhausts."""
    remaining: list[_Candidate] = list(cands)
    party_members: list[list[_Assignment]] = [[] for _ in range(K)]
    party_exp: list[int] = [0] * K

    # Scarcity-first role order for Pass 1 — rarest strong first (typically
    # TANK) so we don't burn a multi-role strong candidate on an easy role
    # before their scarce coverage slot is filled elsewhere.
    strong_count: Counter[Role] = Counter()
    for c in cands:
        strong_count.update(c.strong_roles)
    role_order = sorted(
        CAPABILITY_ROLES, key=lambda r: strong_count[r]
    )

    # --- Pass 1: core coverage --------------------------------------------
    # Rule 5 (experience spread): for each role, process parties in ascending
    # ``party_exp`` order — the lowest-exp party picks first, so the
    # highest-``success_count`` candidate lands there and the next party gets
    # the next-best. Without this, best-of-role always drains into party 0.
    for role in role_order:
        for p in sorted(range(K), key=lambda i: party_exp[i]):
            # Prefer a strong candidate first (rule 1).
            picked_strong = _pick_best(
                remaining, role, party_exp[p],
                filter_pred=lambda c, r=role: r in c.strong_roles,
            )
            if picked_strong is not None:
                _place(picked_strong, p, role, party_members, party_exp,
                       remaining)
                continue
            # Rule 2 fallback: two softs (both assigned the role — honest
            # about the algorithm's decision). If only one soft is left, take
            # it (rule 6 floor).
            first_soft = _pick_best(
                remaining, role, party_exp[p],
                filter_pred=lambda c, r=role: r in c.soft_roles,
            )
            if first_soft is None:
                # K derivation over-promised — a multi-role candidate got
                # claimed by another role. Signal the abort.
                return None
            _place(first_soft, p, role, party_members, party_exp, remaining)
            second_soft = _pick_best(
                remaining, role, party_exp[p],
                filter_pred=lambda c, r=role: r in c.soft_roles,
            )
            if second_soft is not None:
                _place(second_soft, p, role, party_members, party_exp,
                       remaining)

    # --- Pass 2: doubling by priority --------------------------------------
    # Loop until no round makes progress (or every party is at capacity /
    # the pool is empty). Priority: HEALER > TERTIARY > PRIMARY > SECONDARY
    # > TANK, distributed round-robin across parties.
    while remaining:
        placed_this_round = False
        for role in _DOUBLE_PRIORITY:
            for p in range(K):
                if len(party_members[p]) >= PARTY_CAPACITY:
                    continue
                picked = _pick_best(
                    remaining, role, party_exp[p],
                    filter_pred=lambda c, r=role: (
                        r in c.strong_roles or r in c.soft_roles
                    ),
                )
                if picked is None:
                    continue
                _place(picked, p, role, party_members, party_exp, remaining)
                placed_this_round = True
        # After the priority-list pass, top-off any parties that still have
        # room using capability-less candidates as FILL padding (rule 7:
        # avoid unassigned).
        for p in range(K):
            while len(party_members[p]) < PARTY_CAPACITY and remaining:
                picked = _pick_fill(remaining, party_exp[p])
                if picked is None:
                    break
                _place(picked, p, Role.FILL, party_members, party_exp,
                       remaining)
                placed_this_round = True
        if not placed_this_round:
            break

    # Flatten to a plan.
    assignments: list[_Assignment] = []
    for members in party_members:
        assignments.extend(members)
    return _Plan(K=K, assignments=assignments)


def _place(
    c: _Candidate,
    party_idx: int,
    role: Role | None,
    party_members: list[list[_Assignment]],
    party_exp: list[int],
    remaining: list[_Candidate],
) -> None:
    """Record ``c`` into ``party_idx`` at role ``role`` and remove them from
    the remaining pool. ``sort_index`` is the running position within the
    party — buckets._upsert renumbers destination lanes anyway, but a
    monotonic hint keeps the pre-render order deterministic."""
    sort_index = len(party_members[party_idx])
    party_members[party_idx].append(_Assignment(
        uuid=c.uuid,
        party_idx=party_idx,
        role=role,
        sort_index=sort_index,
    ))
    party_exp[party_idx] += c.exp_score
    remaining.remove(c)


def _pick_fill(
    pool: list[_Candidate], party_exp_sum: int
) -> _Candidate | None:
    """Best FILL-slot candidate: someone with no core capabilities (their
    caps are already exhausted for the roles they can play, or they have
    none at all). Sort by low exp_score first (rule 5 spread) then by low
    party_exp_sum first (implicit in the caller's per-party loop)."""
    fill_eligible = [c for c in pool if not c.strong_roles and not c.soft_roles]
    if not fill_eligible:
        return None
    fill_eligible.sort(key=lambda c: (c.exp_score, party_exp_sum))
    return fill_eligible[0]
