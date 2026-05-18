"""App1 — the logged-in user dashboard (``/me``).

Two modules (spec "User Dashboard"):

* **General** (any anni): Registration Status (membership + Core/Fill +
  attendance-likelihood bar) and Role Capacity (≤5 capability rows + add).
* **Specific** (blank when the stamp is in the past, else the current anni):
  prominent countdown, RSVP Status + presence bar, Tentative Information.

View-models are built *here* into plain dicts — templates never touch a lazy
Tortoise relation (Jinja can't ``await``). The Specific module is also served
on its own for the ~15 s HTMX refresh.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.constants import BUCKET_LABEL, PARTY_STAGE_LABELS, AttendanceNotice
from app.db.lifecycle import get_active_event
from app.db.models import BoardPlacement, RoleCapability, Rsvp
from app.domain import attendance, capability, identity, membership, presence
from app.domain.colourblind import role_chip, status_chip
from app.domain.roles import guidance
from app.web import auth
from app.web.deps import render

logger = logging.getLogger("anni.web.user")
router = APIRouter()


def _state(request: Request):
    return request.app.state.appstate


def _avatar(uuid: str, size: int = 48) -> str:
    """Face render for a person. mc-heads is the most reliable free renderer
    (crafatar is frequently down/ratelimited); templates also ``onerror``-
    remove it so a miss degrades cleanly, no broken-image box."""
    return f"https://mc-heads.net/avatar/{uuid}/{size}"


async def _capability_rows(player) -> list[dict]:
    caps = (
        await RoleCapability.filter(player=player)
        .prefetch_related("weapons")
        .order_by("role")
    )
    rows: list[dict] = []
    for c in caps:
        rows.append(
            {
                "id": str(c.id),
                "role": c.role,
                "role_label": guidance(c.role).title,
                "chip": role_chip(c.role),
                "confidence": c.confidence,
                "build_quality": c.build_quality,
                "success_count": c.success_count,
                "weapons": [
                    {"name": w.weapon_name, "subtype": w.weapon_subtype}
                    for w in c.weapons
                ],
            }
        )
    return rows


def _build_specific(player, event, rsvp, placement, st) -> dict | None:
    """The Specific-module view-model, or ``None`` to render it blank."""
    now = int(time.time())
    if event is None or event.stamp_epoch <= now:
        return None  # spec: blank when the stamp is in the past

    seconds = event.stamp_epoch - now
    stored: AttendanceNotice | None = rsvp.notice if rsvp else None

    party = placement.party if placement else None
    online = st.is_online(player.mc_uuid)
    pv = presence.view(
        presence.PresenceInputs(
            online=online is not None,
            queued=bool(online and online.queued),
            api_disabled=identity.is_api_disabled(player.last_online),
            rsvp_notice=stored,
            has_party=party is not None,
            party_world=party.world if party else None,
            party_created=party is not None,
            current_server=None,  # no per-player server signal until App4
            in_party_confirmed=False,
            seconds_to_anni=seconds,
        )
    )

    # Where are you *right now* (separate from RSVP). online_merge rarely
    # knows a non-staff player's server in Phase 1, so we show what we can
    # truthfully say: online / in-queue / offline / API-disabled.
    if online is None:
        if identity.is_api_disabled(player.last_online):
            online_state = {"kind": "unknown",
                            "text": "Status unknown — your Wynncraft API is disabled"}
        else:
            online_state = {"kind": "off", "text": "Offline"}
    elif online.queued:
        online_state = {"kind": "queue", "text": "Online — connecting (in queue)"}
    elif online.server:
        online_state = {"kind": "on", "text": f"Online — {online.server}"}
    else:
        online_state = {"kind": "on", "text": "Online"}

    tentative: dict | None = None
    if placement is not None:
        # Tentative only cares *where* they sit (party vs bucket). Lateness is
        # a participation status, surfaced via "Given Notice" below — not here.
        if party is not None:
            party_label = f"Party {party.ordinal}"
        else:
            party_label = BUCKET_LABEL.get(placement.bucket, "Unassigned")
        tentative = {
            "party_label": party_label,
            "party_ordinal": party.ordinal if party else None,
            "party_host": party.host.mc_username if party and party.host else None,
            "party_host_avatar": (
                _avatar(party.host.mc_uuid, 24) if party and party.host else None
            ),
            "world": party.world if party else None,
            "stage": party.stage if party else None,
            "stage_label": PARTY_STAGE_LABELS.get(party.stage) if party else None,
            "stage_finalised": bool(party and party.stage >= 5),
            "assigned_role": placement.assigned_role,
            "role_chip": role_chip(placement.assigned_role),
            "is_late": placement.is_late,
            "bucket": placement.bucket,
        }

    # "Given Notice" precedence: a staff LATE flag is an *observed fact* and
    # overrides the rosy countdown projection (otherwise a late joiner reads
    # "you showed up on time!" AND is in the LATE bucket — contradictory).
    # Then a stored RSVP; otherwise the on-time/late countdown projection.
    if placement is not None and placement.is_late:
        notice = AttendanceNotice.ATTEND_LATE
    else:
        notice = stored or attendance.project_notice(seconds)
    return {
        "stamp_epoch": event.stamp_epoch,
        "seconds": seconds,
        "rsvp_notice": stored,
        "rsvp_label": _NOTICE_LABEL.get(notice, notice.value),
        # Joined-late is a participation status, so it surfaces here in
        # "Given Notice" — never in Tentative Information (which only cares
        # that they're unassigned).
        "rsvp_phrase": _RSVP_PHRASE.get(notice, notice.value),
        "online_state": online_state,
        "presence": pv,
        "status_chip": status_chip(pv.status),
        "tentative": tentative,
    }


_RSVP_PHRASE = {
    AttendanceNotice.ATTEND_EARLY: "You showed up on time!",
    AttendanceNotice.RSVP_HARD: "You have hard-RSVP'd!",
    AttendanceNotice.RSVP_SOFT: "You have soft-RSVP'd!",
    AttendanceNotice.ATTEND_LATE: "You showed up late",
}


_NOTICE_LABEL = {
    AttendanceNotice.ATTEND_EARLY: "Here ~1 hr early (projected)",
    AttendanceNotice.RSVP_HARD: "Hard RSVP",
    AttendanceNotice.RSVP_SOFT: "Soft RSVP",
    AttendanceNotice.ATTEND_LATE: "Late (projected)",
}

#: One clean clause per effective notice for the General-module sentence.
#: ``effective_notice`` never yields ATTEND_LATE for an RSVP'd user (a stored
#: RSVP outranks the late projection), so an RSVP'd user is never called
#: "late" — their RSVP is an intention to attend, even if a little late.
_GEN_NOTICE_PHRASE = {
    AttendanceNotice.ATTEND_EARLY: "you'd log on about an hour early",
    AttendanceNotice.RSVP_HARD: "you've hard-RSVP'd",
    AttendanceNotice.RSVP_SOFT: "you've soft-RSVP'd",
    AttendanceNotice.ATTEND_LATE: "you'd log on late (no RSVP, under an hour's notice)",
}

#: No effective notice at all: a non-trackable tier (Community/Ally/Other)
#: with no RSVP. They have no "just show up" option — ``effective_notice``
#: returns ``None`` and the table cannot prioritise them until they RSVP.
_NO_NOTICE_PHRASE = "you haven't RSVP'd (community attendance can't be tracked unless you do)"


async def build_dashboard(request: Request, player) -> dict:
    """Full ``/me`` context (General + Specific)."""
    st = _state(request)
    event = await get_active_event()

    cap_rows = await _capability_rows(player)
    is_core = capability.is_core(len(cap_rows))

    rsvp = placement = None
    if event is not None:
        rsvp = await Rsvp.filter(
            event=event, player=player, revoked_at=None
        ).first()
        placement = (
            await BoardPlacement.filter(event=event, player=player)
            .select_related("party", "party__host")
            .first()
        )

    now = int(time.time())
    seconds = (
        event.stamp_epoch - now if event and event.stamp_epoch > now else None
    )
    stored: AttendanceNotice | None = rsvp.notice if rsvp else None
    notice = attendance.effective_notice(
        stored, seconds, tier=player.membership_tier
    )
    pct = attendance.evaluate(
        player.membership_tier, core=is_core, notice=notice
    )
    band, like_label = attendance.meta(pct)

    desynced = bool(
        player.wynn_username and player.wynn_username != player.mc_username
    )
    specific = _build_specific(player, event, rsvp, placement, st)
    logger.debug(
        "dashboard %s: tier=%s %s caps=%d like=%s event=%s%s",
        player.mc_username, player.membership_tier.value,
        "Core" if is_core else "Fill", len(cap_rows), like_label,
        event is not None,
        f" presence={specific['presence'].status.value}" if specific else "",
    )
    return {
        "player": {
            "mc_uuid": player.mc_uuid,
            "mc_username": player.mc_username,
            "wynn_username": player.wynn_username,
            "desynced": desynced,
            "guild": player.guild,
            "avatar": _avatar(player.mc_uuid),
            "has_password": bool(player.password_hash),
        },
        "membership": {
            "tier": player.membership_tier,
            "label": membership.label(player.membership_tier),
        },
        "eligibility": "Core" if is_core else "Fill",
        "is_core": is_core,
        "fill_warning": None if is_core else capability.FILL_WARNING,
        "attendance": {
            "band": band,
            "label": like_label,
            "notice_phrase": _GEN_NOTICE_PHRASE.get(notice, _NO_NOTICE_PHRASE),
        },
        "capabilities": cap_rows,
        "specific": specific,
        "has_event": event is not None,
    }


@router.get("/me")
async def dashboard(request: Request):
    player = await auth.current_user(request)
    if player is None:
        return RedirectResponse("/", status_code=303)
    ctx = await build_dashboard(request, player)
    return render(request, "user/dashboard.html", **ctx)


@router.get("/me/specific", include_in_schema=False)
async def specific_fragment(request: Request):
    """HTMX poll target — just the Specific module (countdown/RSVP/presence)."""
    player = await auth.current_user(request)
    if player is None:
        return RedirectResponse("/", status_code=303)
    ctx = await build_dashboard(request, player)
    return render(request, "user/_specific.html", **ctx)
