"""Board view-model — the one JSON-able snapshot shape.

Built once, consumed twice and identically: the organizer router renders the
initial board server-side from it (Jinja), and ``board_hub`` ships the *same*
dict over the socket (``WELCOME`` + ``PATCH``). Keeping a single shape is what
makes "refresh == identical snapshot" hold and stops the SSR board and the
live board drifting apart.

Presentation only (chips/avatar/labels). Mutation is ``domain/buckets``;
presence *computation* is ``services/presence_poller`` — here we just read its
last-good ``state.presence_by_uuid`` (default ``UNKNOWN`` until the first tick
lands, which the WS PATCH then corrects within a cadence).
"""

from __future__ import annotations

from app.constants import (
    BUCKET_LABEL,
    PARTY_STAGE_LABELS,
    PARTY_CAPACITY,
    ROLE_STYLES,
    BucketKind,
    PartyResult,
    PresenceStatus,
    Role,
    RsvpState,
)
from app.domain import buckets
from app.domain import regions as regions_domain
from app.domain import rsvp as rsvp_domain
from app.domain.colourblind import role_chip, rsvp_chip, status_chip
from app.domain.membership import label as tier_label
from app.domain.schedule import phase_of
from app.services import hot_window
from app.services.state import AppState
from app.settings import get_settings


#: Single-letter glyph per capability role for the colourblind variant. "M" for
#: tertiary (Mob killer) distinguishes it from Tank — same convention as
#: ``constants.ROLE_STYLES`` uses ``HDMG`` rather than ``TER``.
_CAP_LETTER: dict[Role, str] = {
    Role.PRIMARY: "P",
    Role.SECONDARY: "S",
    Role.TERTIARY: "M",
    Role.HEALER: "H",
    Role.TANK: "T",
}
#: CSS custom property for the dot's *raw* role hue (the same one ``role_chip``
#: returns as ``css_var`` — full red/yellow/magenta/green/blue, body.cb swaps
#: them to the Okabe-Ito set).
_CAP_CSS_VAR: dict[Role, str] = {
    Role.PRIMARY: "--role-primary",
    Role.SECONDARY: "--role-secondary",
    Role.TERTIARY: "--role-tertiary",
    Role.HEALER: "--role-healer",
    Role.TANK: "--role-tank",
}
#: Paired *-light* alias for the same role — used by the dot's halo so the
#: outline is the same hue family as the fill, just lighter (a dark fill on a
#: dark card disappears without this). CB-swapped (see colourblind.css).
_CAP_CSS_VAR_LIGHT: dict[Role, str] = {
    role: f"{var}-light" for role, var in _CAP_CSS_VAR.items()
}
#: Confidence ranking for dot order. Mirrors :class:`ConfidenceLevel` (low =
#: least confident, high = most), inverted so the sort key reads left-to-right
#: as best-first. Unknown / off-table values fall to the end.
_CONFIDENCE_RANK: dict[str, int] = {"high": 0, "moderate": 1, "low": 2}


def _capability_dots(caps) -> list[dict]:
    """Shape a player's :class:`RoleCapability` rows for the person-card dots
    + their hover/click popovers. Skips anything not in
    :data:`CAPABILITY_ROLES` (FILL is assign-only, never a capability) so a
    stale row can't leak in.

    Sorted **per-user** by self-declared confidence (high → low), with
    lifetime wins (``success_count``) as the tiebreaker. The dot row then
    reads as the player's own preference ranking — the leftmost dot is what
    they'd most like to slot into — which is more useful to an organiser
    than a fixed PRIM/SUNK/HDMG/HEAL/TANK column where every card looks the
    same regardless of fit.
    """
    dots: list[dict] = []
    for c in caps:
        if c.role not in _CAP_CSS_VAR:
            continue
        dots.append({
            "role": c.role.value,
            "label": ROLE_STYLES[c.role].label,
            "letter": _CAP_LETTER[c.role],
            "css_var": _CAP_CSS_VAR[c.role],
            "css_var_light": _CAP_CSS_VAR_LIGHT[c.role],
            "confidence": c.confidence.value,
            "build_quality": c.build_quality.value,
            "success_count": c.success_count,
            "weapons": [
                {"name": w.weapon_name, "subtype": w.weapon_subtype}
                for w in c.weapons
            ],
        })
    # Higher confidence first; more wins breaks ties (negate so larger wins
    # sort earlier under ascending sort).
    dots.sort(key=lambda d: (
        _CONFIDENCE_RANK.get(d["confidence"], 99),
        -d["success_count"],
    ))
    return dots


#: Presence statuses that count as "not here" for the offline-soft lane.
#: UNKNOWN is excluded on purpose — unconfirmable is not the same as absent,
#: and parking someone in the "don't count on them" lane on a *guess* is
#: exactly the fabrication the spec forbids.
_OFFLINE_STATUSES: frozenset[str] = frozenset(
    {PresenceStatus.OFFLINE.value, PresenceStatus.OFFLINE_GONE.value}
)


def is_offline_soft(person: dict) -> bool:
    """The "offline soft RSVPs" sub-bucket predicate — soft RSVP **and**
    currently offline.

    Purely *derived*: there is no stored lane flag for it (unlike walk-in /
    late, which record how someone arrived). It has to be, because the whole
    point is that it tracks live presence — a soft RSVP logging on has to
    leave the lane on the next presence tick without anyone dragging them,
    and party members have no lane flags at all.

    The practical consequence for drag-and-drop: the lane's dropzone targets
    the *same* container as the lane above it (the party, or the main
    Unassigned lane), so dropping a card in or out of it is a no-op that
    re-derives on the next render. That is intended — it's a view of a
    container, not a container.
    """
    return (
        person["rsvp_state"] == RsvpState.SOFT.value
        and person["status"] in _OFFLINE_STATUSES
    )


def avatar(uuid: str, size: int = 40) -> str:
    """Face render (mirrors ``routers/user._avatar`` — mc-heads is the most
    reliable free renderer; templates also ``onerror``-remove the <img>)."""
    return f"https://mc-heads.net/avatar/{uuid}/{size}"


def _person(
    row: dict,
    presence_by_uuid: dict[str, str],
    rsvp_states: dict[str, RsvpState],
) -> dict:
    """One person-object card. Carries the colour-independent channels (glyph,
    label, border pattern, the name, regions text) so it reads with no colour
    at all — the spec's colourblind hard rule, via the shared chip builders.

    ``rsvp_state``/``rsvp_chip`` are the card's second, independent axis (the
    badge left of the avatar): the status border says *where they are*, the
    badge says *what they promised*. Both follow the player wherever their
    placement currently sits (Unassigned-LATE, wontassign, or a party).
    ``rsvp_revoked`` is kept as the plain boolean the "Retracted" text pill
    reads. ``is_placeholder`` propagates the auto-promoter's "stub card" flag
    straight from ``AnniPlayer.is_placeholder``.
    """
    try:
        status = PresenceStatus(presence_by_uuid.get(row["uuid"], "unknown"))
    except ValueError:  # a stale/unknown cached value never breaks the board
        status = PresenceStatus.UNKNOWN
    rsvp_state = rsvp_states.get(row["uuid"], RsvpState.NONE)
    return {
        "uuid": row["uuid"],
        "name": row["mc_username"],
        "wynn_username": row["wynn_username"],
        "desynced": row["desynced"],
        "avatar": avatar(row["uuid"]),
        "tier": row["tier"],
        "tier_label": tier_label(row["tier"]),
        "regions": regions_domain.labelled(row["preferred_regions"]),
        "assigned_role": row["assigned_role"],
        "role_chip": role_chip(row["assigned_role"]),
        "status": status.value,
        "status_chip": status_chip(status),
        "is_late": row["is_late"],
        "is_walkin": row.get("is_walkin", False),
        "is_placeholder": row.get("is_placeholder", False),
        "rsvp_state": rsvp_state.value,
        "rsvp_chip": rsvp_chip(rsvp_state),
        "rsvp_revoked": rsvp_state is RsvpState.REVOKED,
        "sort_index": row["sort_index"],
        "capability_dots": _capability_dots(row.get("capabilities") or []),
    }


async def snapshot(event, state: AppState) -> dict:
    """The whole board for ``event`` as one JSON-able dict (``{}``-safe event
    fields when there is nothing announced is the caller's concern — this is
    only called with a real event)."""
    settings = get_settings()
    grace_seconds = max(0, settings.grace_hours) * 3600
    phase = phase_of(event.stamp_epoch, grace_seconds)

    rows = await buckets.board_rows(event)
    pres = state.presence_by_uuid
    # The RSVP axis for every player with a row (revoked included — that IS a
    # state). Built once per snapshot so every _person() call is O(1).
    rsvp_states = await rsvp_domain.states_by_uuid(event)
    by_party: dict[str, list[dict]] = {}
    bucket_members: dict[str, list[dict]] = {
        BucketKind.UNASSIGNED.value: [],
        BucketKind.VOLUNTEERS.value: [],
        BucketKind.WONTASSIGN.value: [],
    }
    for row in rows:
        person = _person(row, pres, rsvp_states)
        if row["party_id"]:
            by_party.setdefault(row["party_id"], []).append(person)
        elif row["bucket"] in bucket_members:
            bucket_members[row["bucket"]].append(person)

    parties = []
    for p in await buckets.parties_of(event):
        all_members = sorted(
            by_party.get(str(p.id), []), key=lambda m: m["sort_index"]
        )
        # Same derived split as Unassigned: soft RSVPs who aren't online get
        # their own sub-bucket so an organiser can see, at a glance, which of
        # a party's ten slots are being held by someone who only said "maybe"
        # and hasn't shown up. ``count`` still spans both — they are party
        # members either way, and the N/10 header must not lie.
        members = [m for m in all_members if not is_offline_soft(m)]
        offline_soft = [m for m in all_members if is_offline_soft(m)]
        parties.append(
            {
                "id": str(p.id),
                "ordinal": p.ordinal,
                "host": (
                    {"name": p.host.mc_username, "avatar": avatar(p.host.mc_uuid, 24)}
                    if p.host
                    else None
                ),
                "host_uuid": p.host.mc_uuid if p.host else None,
                "world": p.world,
                "stage": p.stage,
                "stage_label": PARTY_STAGE_LABELS.get(p.stage, ""),
                "result": p.result.value,
                "capacity": PARTY_CAPACITY,
                "members": members,
                "offline_soft": offline_soft,
                "count": len(all_members),
            }
        )

    unassigned = sorted(
        bucket_members[BucketKind.UNASSIGNED.value],
        key=lambda m: m["sort_index"],
    )
    # Flat de-duped {uuid,name} for the host / organiser <select>s (everyone
    # currently on the board, ordered by name) + the current organiser even
    # if they aren't placed.
    roster: dict[str, str] = {r["uuid"]: r["mc_username"] for r in rows}
    organizer = None
    if event.organizer:
        roster.setdefault(event.organizer.mc_uuid, event.organizer.mc_username)
        organizer = {
            "uuid": event.organizer.mc_uuid,
            "name": event.organizer.mc_username,
            "avatar": avatar(event.organizer.mc_uuid, 24),
        }
    monitoring = hot_window.monitoring_state(
        event,
        hot_window_open_seconds=settings.hot_window_open_seconds,
        grace_seconds=grace_seconds,
    )
    return {
        "event": {
            "stamp_epoch": event.stamp_epoch,
            "phase": phase.value,
            "frozen": phase.value == "grace",
            "organizer": organizer,
            "monitoring": monitoring,
            "monitoring_label": hot_window.MONITORING_LABEL[monitoring],
        },
        "parties": parties,
        # UNASSIGNED has four sub-buckets, rendered in this order:
        #   on_time      — RSVP'd main lane
        #   offline_soft — DERIVED: soft RSVP + offline, carved out of the
        #                  main lane only (see ``is_offline_soft``). Walk-ins
        #                  by definition never RSVP'd, and LATE is a stronger
        #                  provenance marker worth keeping intact, so neither
        #                  of those lanes feeds it.
        #   walkin       — auto-detected non-RSVP arrivals, T-70..T-60
        #   late         — anything placed after T-60 (LATE wins if both flags
        #                  are set)
        "buckets": {
            BucketKind.UNASSIGNED.value: {
                "label": BUCKET_LABEL[BucketKind.UNASSIGNED],
                "on_time": [
                    m for m in unassigned
                    if not m["is_late"] and not m["is_walkin"]
                    and not is_offline_soft(m)
                ],
                "offline_soft": [
                    m for m in unassigned
                    if not m["is_late"] and not m["is_walkin"]
                    and is_offline_soft(m)
                ],
                "walkin": [
                    m for m in unassigned
                    if not m["is_late"] and m["is_walkin"]
                ],
                "late": [m for m in unassigned if m["is_late"]],
            },
            BucketKind.VOLUNTEERS.value: {
                "label": BUCKET_LABEL[BucketKind.VOLUNTEERS],
                "members": sorted(
                    bucket_members[BucketKind.VOLUNTEERS.value],
                    key=lambda m: m["sort_index"],
                ),
            },
            BucketKind.WONTASSIGN.value: {
                "label": BUCKET_LABEL[BucketKind.WONTASSIGN],
                "members": sorted(
                    bucket_members[BucketKind.WONTASSIGN.value],
                    key=lambda m: m["sort_index"],
                ),
            },
        },
        "results": [r.value for r in PartyResult],
        "roster": sorted(
            ({"uuid": u, "name": n} for u, n in roster.items()),
            key=lambda x: x["name"].lower(),
        ),
        # Lead-organiser candidates: the FULL WAPI guild-staff list (online or
        # not), + the current organiser even if their rank fell out of the
        # staff set, so the selected option always renders. Uses the already-
        # resolved ``organizer`` dict (never the raw FK — an unfetched null FK
        # is a truthy _NoneAwaitable, not None).
        "organizer_candidates": _organizer_candidates(state, organizer),
    }


def _organizer_candidates(state: AppState, organizer: dict | None) -> list[dict]:
    cands: dict[str, str] = {
        u: s["username"] for u, s in state.guild_staff.items()
    }
    if organizer:
        cands.setdefault(organizer["uuid"], organizer["name"])
    return sorted(
        ({"uuid": u, "name": n} for u, n in cands.items()),
        key=lambda x: x["name"].lower(),
    )
