"""Presence state machine — "how we see you right now" for the active anni.

Pure: every input is passed in. The live diff-and-broadcast *poller* is
Phase 2; this module is the rule it will use and is what the Phase-1 user
dashboard's Specific module renders on each request.

Status mapping (authoritative: ``.claude/domain_rules.md``):

* API disabled **and** unconfirmable           -> ``UNKNOWN`` (never faked
  online; if the online-merge actually shows them, that's confirmation and we
  fall through to the online branch).
* online + ``queued``                          -> ``ONLINE_ELSEWHERE`` (anni
  is queue-heavy; queued == connecting, never ``OFFLINE_*``).
* online, no party assigned                     -> ``ONLINE_ELSEWHERE``.
* online, on the party's world, in party        -> ``ONLINE_PARTY``.
* online, on the party's world, not in party    -> ``ONLINE_WORLD``.
* online, wrong/unknown world                   -> ``ONLINE_ELSEWHERE``.
* offline + hard RSVP                            -> ``OFFLINE_HARD``.
* offline + soft RSVP                            -> ``OFFLINE_SOFT``.
* offline, no RSVP                               -> ``OFFLINE_GONE``.

The "was here ≤ T-60m then left" refinement of ``OFFLINE_GONE`` needs
presence *history*; that lands with the Phase-2 ``presence_poller``. Phase 1
maps offline-with-no-RSVP straight to ``OFFLINE_GONE`` (the correct status —
only the early-vs-late nuance is deferred).

The user dashboard shows a status *bar* whose colour/flash escalates with the
countdown (staff borders are Phase 2). Bar rules (``.claude/domain_rules.md``):
GONE flashes immediately; HARD red, flashes from T-20m; SOFT red, flashes
from T-45m; ELSEWHERE/WORLD green→yellow once the world/party is announced;
PARTY green; UNKNOWN yellow.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.constants import AttendanceNotice, PresenceStatus

_T20M = 20 * 60
_T45M = 45 * 60


def normalize_world(s: str | None) -> str | None:
    """Canonical form for comparing a party's announced world to a player's
    current server. WAPI reports e.g. ``"WC1"``; staff type ``"1"``,
    ``"wc1"``, ``"01"``, sometimes with stray whitespace. Both sides go
    through here so a string mismatch can't hide a real world match.

    Returns ``"WC<n>"`` for numeric worlds; non-numeric server names
    (lobby/hub/etc.) pass through uppercased so they don't silently match
    a numbered world. ``None``/empty → ``None``.
    """
    if not s:
        return None
    t = s.strip().upper().removeprefix("WC").strip().lstrip("0")
    if t.isdigit():
        return f"WC{t}"
    canonical = s.strip().upper()
    return canonical or None


@dataclass(frozen=True)
class PresenceInputs:
    """Everything the machine needs. Defaults model the common Phase-1 case
    (we rarely know a player's current server without vetsmod corroboration)."""

    online: bool = False
    queued: bool = False
    api_disabled: bool = False
    rsvp_notice: AttendanceNotice | None = None  # stored RSVP_HARD/RSVP_SOFT
    has_party: bool = False
    party_world: str | None = None
    party_created: bool = False
    current_server: str | None = None     # where they are now (often unknown)
    in_party_confirmed: bool = False      # App4 vetsmod corroboration only
    seconds_to_anni: int | None = None


@dataclass(frozen=True)
class PresenceView:
    """Status + the user-dashboard bar descriptor for that status."""

    status: PresenceStatus
    bar_class: str   # maps to a .bar-* CSS class
    message: str
    flash: bool


def classify(i: PresenceInputs) -> PresenceStatus:
    """The :class:`PresenceStatus` for these inputs (the core rule)."""
    if i.online:
        if i.queued or not i.has_party:
            return PresenceStatus.ONLINE_ELSEWHERE
        if i.party_world and i.current_server:
            if normalize_world(i.current_server) != normalize_world(i.party_world):
                return PresenceStatus.ONLINE_ELSEWHERE
            return (
                PresenceStatus.ONLINE_PARTY
                if i.in_party_confirmed
                else PresenceStatus.ONLINE_WORLD
            )
        # Party assigned but we can't confirm the world (no server signal).
        return PresenceStatus.ONLINE_ELSEWHERE

    # Offline. API-disabled + can't confirm online == genuinely unknown.
    if i.api_disabled:
        return PresenceStatus.UNKNOWN
    if i.rsvp_notice == AttendanceNotice.RSVP_HARD:
        return PresenceStatus.OFFLINE_HARD
    if i.rsvp_notice == AttendanceNotice.RSVP_SOFT:
        return PresenceStatus.OFFLINE_SOFT
    return PresenceStatus.OFFLINE_GONE


def _flash(status: PresenceStatus, seconds_to_anni: int | None) -> bool:
    if status == PresenceStatus.OFFLINE_GONE:
        return True  # subtly flashing immediately
    if seconds_to_anni is None:
        return False
    if status == PresenceStatus.OFFLINE_HARD:
        return seconds_to_anni <= _T20M
    if status == PresenceStatus.OFFLINE_SOFT:
        return seconds_to_anni <= _T45M
    return False


def view(i: PresenceInputs) -> PresenceView:
    """Status + bar class/message/flash for the user dashboard."""
    status = classify(i)
    flash = _flash(status, i.seconds_to_anni)

    if status == PresenceStatus.OFFLINE_GONE:
        return PresenceView(status, "bar-danger",
                            "We saw you around but you're not online now — "
                            "log back on before the anni.", flash)
    if status == PresenceStatus.OFFLINE_HARD:
        return PresenceView(status, "bar-danger",
                            "You hard-RSVP'd but aren't online yet.", flash)
    if status == PresenceStatus.OFFLINE_SOFT:
        return PresenceView(status, "bar-danger",
                            "You soft-RSVP'd but aren't online yet.", flash)
    if status == PresenceStatus.ONLINE_ELSEWHERE:
        if i.party_world:
            return PresenceView(status, "bar-warn",
                                f"Online — head to your party's world "
                                f"({i.party_world}).", flash)
        return PresenceView(status, "bar-ok",
                            "Online — waiting on your party/world assignment.",
                            flash)
    if status == PresenceStatus.ONLINE_WORLD:
        if i.party_created:
            return PresenceView(status, "bar-warn",
                                "On the right world — join your party now.",
                                flash)
        return PresenceView(status, "bar-ok",
                            "On the right world — waiting for the party to "
                            "be created.", flash)
    if status == PresenceStatus.ONLINE_PARTY:
        return PresenceView(status, "bar-ok",
                            "In your party on the right world — you're set!",
                            flash)
    # UNKNOWN
    return PresenceView(status, "bar-warn",
                        "Your Wynncraft API is disabled and we can't confirm "
                        "your status — connect via vetsmod or enable the API.",
                        flash)
