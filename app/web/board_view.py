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
    CAPABILITY_ROLES,
    PARTY_STAGE_LABELS,
    PARTY_CAPACITY,
    ROLE_STYLES,
    BucketKind,
    PartyResult,
    PresenceStatus,
    Role,
)
from app.domain import buckets
from app.domain import regions as regions_domain
from app.domain.colourblind import role_chip, status_chip
from app.domain.membership import label as tier_label
from app.domain.schedule import phase_of
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
#: Stable display order for the dots (left-to-right). Matches the enum order
#: in ``Role`` so the layout is identical across all cards regardless of which
#: capabilities a given player has.
_CAP_ORDER: dict[Role, int] = {r: i for i, r in enumerate(CAPABILITY_ROLES)}


def _capability_dots(caps) -> list[dict]:
    """Shape a player's :class:`RoleCapability` rows for the person-card dots
    + their hover/click popovers. Skips anything not in :data:`CAPABILITY_ROLES`
    (FILL is assign-only, never a capability) so a stale row can't leak in."""
    dots: list[dict] = []
    for c in caps:
        if c.role not in _CAP_ORDER:
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
    dots.sort(key=lambda d: _CAP_ORDER[Role(d["role"])])
    return dots


def avatar(uuid: str, size: int = 40) -> str:
    """Face render (mirrors ``routers/user._avatar`` — mc-heads is the most
    reliable free renderer; templates also ``onerror``-remove the <img>)."""
    return f"https://mc-heads.net/avatar/{uuid}/{size}"


def _person(row: dict, presence_by_uuid: dict[str, str]) -> dict:
    """One person-object card. Carries the colour-independent channels (glyph,
    label, border pattern, the name, regions text) so it reads with no colour
    at all — the spec's colourblind hard rule, via the shared chip builders."""
    try:
        status = PresenceStatus(presence_by_uuid.get(row["uuid"], "unknown"))
    except ValueError:  # a stale/unknown cached value never breaks the board
        status = PresenceStatus.UNKNOWN
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
    by_party: dict[str, list[dict]] = {}
    bucket_members: dict[str, list[dict]] = {
        BucketKind.UNASSIGNED.value: [],
        BucketKind.VOLUNTEERS.value: [],
        BucketKind.WONTASSIGN.value: [],
    }
    for row in rows:
        person = _person(row, pres)
        if row["party_id"]:
            by_party.setdefault(row["party_id"], []).append(person)
        elif row["bucket"] in bucket_members:
            bucket_members[row["bucket"]].append(person)

    parties = []
    for p in await buckets.parties_of(event):
        members = sorted(
            by_party.get(str(p.id), []), key=lambda m: m["sort_index"]
        )
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
    return {
        "event": {
            "stamp_epoch": event.stamp_epoch,
            "phase": phase.value,
            "frozen": phase.value == "grace",
            "organizer": organizer,
        },
        "parties": parties,
        # UNASSIGNED carries the LATE sub-bucket; the other two are flat.
        "buckets": {
            BucketKind.UNASSIGNED.value: {
                "label": BUCKET_LABEL[BucketKind.UNASSIGNED],
                "on_time": [m for m in unassigned if not m["is_late"]],
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
