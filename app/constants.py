"""Domain enums + data tables.

This module is the single source of truth for the *vocabulary* of the system:
roles, membership tiers, presence statuses, the colour palettes (default +
colourblind-safe), the attendance-priority table, and the role guidance text.

Pure data only — no DB, FastAPI, or discord imports — so the domain layer and
tests can use it freely. Colours are NEVER the only signal: every role/status
also carries a short glyph + label + (for statuses) a border pattern so the
colourblind variant is fully usable (spec hard requirement).

Sources:
* Role/status colours — see .claude/spec.md ([^5]/[^6]) + concept-art legend.
* Attendance table & role guidance — https://www.wynnvets.org/docs/guild/anni/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

DOCS_BASE = "https://www.wynnvets.org/docs/guild/anni"


# ---------------------------------------------------------------------------
# Core enums
# ---------------------------------------------------------------------------
class Role(StrEnum):
    """The six annihilation roles. FILL is assignable/colourable but is *not*
    a capability a user 'indicates' — it is the absence of core capability."""

    PRIMARY = "primary"      # boss killer
    SECONDARY = "secondary"  # sun killer
    TERTIARY = "tertiary"    # healing-mob killer
    HEALER = "healer"
    TANK = "tank"
    FILL = "fill"


#: Roles a user can register a capability for (the five core roles).
CAPABILITY_ROLES: tuple[Role, ...] = (
    Role.PRIMARY,
    Role.SECONDARY,
    Role.TERTIARY,
    Role.HEALER,
    Role.TANK,
)
#: Roles an organiser can assign on the board (core + FILL).
ASSIGNABLE_ROLES: tuple[Role, ...] = CAPABILITY_ROLES + (Role.FILL,)


class MembershipTier(StrEnum):
    """How aggressively we prioritise a user. Order = priority (see
    ``MEMBERSHIP_PRIORITY``). MEMBER = in the Returners guild; COMMUNITY =
    guildless; ALLY = the configured allied guild; OTHER = any other guild.
    WAITLIST/HONOURARY come from dazebot's tier resolution."""

    MEMBER = "member"
    WAITLIST = "waitlist"
    HONOURARY = "honourary"
    COMMUNITY = "community"
    ALLY = "ally"
    OTHER = "other"


#: Lower number = higher priority. Used by the attendance model + sorting.
MEMBERSHIP_PRIORITY: dict[MembershipTier, int] = {
    MembershipTier.MEMBER: 0,
    MembershipTier.WAITLIST: 1,
    MembershipTier.HONOURARY: 2,
    MembershipTier.COMMUNITY: 3,
    MembershipTier.ALLY: 4,
    MembershipTier.OTHER: 5,
}


class AttendanceNotice(StrEnum):
    """How much warning we have that a user intends to attend. ONE_HR_EARLY is
    derived from presence (they showed up), not stored; HARD/SOFT come from
    ``/rsvp``; LATE = online within 60 min with no RSVP; NONE = nothing."""

    ONE_HR_EARLY = "one_hr_early"
    HARD_RSVP = "hard_rsvp"
    SOFT_RSVP = "soft_rsvp"
    LATE = "late"
    NONE = "none"


class PresenceStatus(StrEnum):
    """How we see a user *right now* for the active anni (maps to the spec's
    user states + footnote [^6] colours).

    An offline person is exactly one of OFFLINE_GONE / OFFLINE_HARD /
    OFFLINE_SOFT — there is no "offline, no RSVP" state: being on the list with
    no RSVP means they are *here*, and once someone is offline the 1hr-early
    vs late distinction is tracked elsewhere and irrelevant (they're just
    gone). UNKNOWN = API-disabled and unconfirmable (never fabricated as
    online)."""

    OFFLINE_GONE = "offline_gone"          # offline (was here / never showed)
    OFFLINE_HARD = "offline_hard"          # offline, hard RSVP
    OFFLINE_SOFT = "offline_soft"          # offline, soft RSVP
    ONLINE_ELSEWHERE = "online_elsewhere"  # online, wrong world
    ONLINE_WORLD = "online_world"          # online, party world, not in party
    ONLINE_PARTY = "online_party"          # online, in the party
    UNKNOWN = "unknown"                    # API-disabled / unconfirmable


#: Queue rule (anni is *very* queue-intensive — a large fraction of players sit
#: in a Wynncraft server queue near event time). A player the online-merge
#: source reports as ``queued`` is *connecting*, NOT gone: presence logic must
#: NEVER mark a queued player ``OFFLINE_GONE``. vetsmod's ``/wv list`` already
#: surfaces queued users from the same source (``/v1/outbound/list`` ``queued``
#: flag), so mirroring that source keeps us correct — verify the flag is
#: actually populated when wiring presence (Phase 2).
QUEUE_NEVER_OFFLINE_GONE = True


class ConfidenceLevel(StrEnum):
    """Self-assessed confidence/preference for a role (and reused for build
    quality). HIGH = confident & enjoys it; LOW = inexperienced/dispreferred."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


# Build quality uses the same three levels but is a distinct concept.
BuildQuality = ConfidenceLevel


class BucketKind(StrEnum):
    """Non-party containers on the organizer board."""

    UNASSIGNED = "unassigned"                       # rsvp'd / 1hr-early, not placed
    CONFIRMED_NONATTENDANCE = "confirmed_nonattendance"
    WILLING_TO_SIT_OUT = "willing_to_sit_out"


class PartyResult(StrEnum):
    PENDING = "pending"
    LOSS = "loss"
    LAG = "lag"
    WIN = "win"


#: Party stage 1..5 with the organiser-facing label (spec "Stages").
PARTY_STAGE_LABELS: dict[int, str] = {
    1: "The organiser hasn't started.",
    2: "Determining how many parties we can support.",
    3: "Determining which core users go in which parties.",
    4: "Determining everyone's roles.",
    5: "Parties finalised — fill slots added/ready.",
}
MIN_PARTY_STAGE, MAX_PARTY_STAGE = 1, 5
PARTY_CAPACITY = 10


# ---------------------------------------------------------------------------
# Colour palettes + non-colour encodings
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoleStyle:
    """Visual encoding for a role *background*. ``glyph``/``label`` make it
    readable without colour; ``cb`` is the colourblind-safe fill."""

    color: str        # default palette (hex)
    cb: str           # colourblind-safe palette (Okabe-Ito derived)
    glyph: str        # short letters shown on the pill/person object
    label: str        # accessible label (aria)


# Default role colours — spec.md [^5] + concept-art legend.
ROLE_STYLES: dict[Role, RoleStyle] = {
    Role.PRIMARY:   RoleStyle("#dc2626", "#D55E00", "PRI", "Primary (boss killer)"),
    Role.SECONDARY: RoleStyle("#eab308", "#F0E442", "SEC", "Secondary (sun killer)"),
    Role.TERTIARY:  RoleStyle("#9333ea", "#CC79A7", "TER", "Tertiary (mob killer)"),
    Role.HEALER:    RoleStyle("#16a34a", "#009E73", "HEA", "Healer"),
    Role.TANK:      RoleStyle("#4f46e5", "#0072B2", "TAN", "Tank"),
    Role.FILL:      RoleStyle("#06b6d4", "#56B4E9", "FIL", "Fill"),
}
#: Unassigned person object background (no role yet).
UNASSIGNED_STYLE = RoleStyle("#9ca3af", "#999999", "—", "Unassigned")


@dataclass(frozen=True)
class StatusStyle:
    """Visual encoding for a person's *status border*. ``pattern`` (solid /
    dashed / dotted / double / thick) is a non-colour channel; ``glyph`` +
    ``label`` carry meaning for the colourblind variant and screen readers.
    Borders are steady — there is no pulsing/flashing border for any status
    (the spec's escalation "flashing" is a bottom-*bar* affordance, not the
    border; see ``.bar-flash`` and ``app.domain.presence``)."""

    color: str
    cb: str
    pattern: str
    glyph: str
    label: str


# Status border colours — spec.md [^6].
STATUS_STYLES: dict[PresenceStatus, StatusStyle] = {
    PresenceStatus.OFFLINE_GONE: StatusStyle(
        "#dc2626", "#D55E00", "solid", "!", "Offline — gone (at risk)"
    ),
    PresenceStatus.OFFLINE_HARD: StatusStyle(
        "#16a34a", "#009E73", "double", "✓", "Offline — hard RSVP (safe for now)"
    ),
    PresenceStatus.OFFLINE_SOFT: StatusStyle(
        "#eab308", "#F0E442", "dashed", "~", "Offline — soft RSVP"
    ),
    PresenceStatus.ONLINE_ELSEWHERE: StatusStyle(
        "#2563eb", "#0072B2", "dotted", "→", "Online — wrong world"
    ),
    PresenceStatus.ONLINE_WORLD: StatusStyle(
        "#4f46e5", "#56B4E9", "dash-dot", "◐", "Online — party world, not in party"
    ),
    PresenceStatus.ONLINE_PARTY: StatusStyle(
        "#9333ea", "#CC79A7", "thick", "●", "Online — in party"
    ),
    PresenceStatus.UNKNOWN: StatusStyle(
        "#6b7280", "#777777", "dotted", "?", "Unknown — API disabled / unconfirmable"
    ),
}


# ---------------------------------------------------------------------------
# Attendance likelihood (the bottom bar on the dashboard)
# ---------------------------------------------------------------------------
class Likelihood(StrEnum):
    VIRTUALLY_GUARANTEED = "virtually_guaranteed"
    MORE_OFTEN_THAN_NOT = "more_often_than_not"
    FREQUENTLY = "frequently"
    OFTEN = "often"
    SOMETIMES = "sometimes"
    RARELY = "rarely"
    UNLIKELY = "unlikely"


#: Bar fill percentage + human label per likelihood (best -> worst).
LIKELIHOOD_META: dict[Likelihood, tuple[int, str]] = {
    Likelihood.VIRTUALLY_GUARANTEED: (95, "Virtually guaranteed"),
    Likelihood.MORE_OFTEN_THAN_NOT: (75, "More often than not"),
    Likelihood.FREQUENTLY: (60, "Frequently"),
    Likelihood.OFTEN: (45, "Often"),
    Likelihood.SOMETIMES: (25, "Sometimes"),
    Likelihood.RARELY: (10, "Rarely"),
    Likelihood.UNLIKELY: (3, "Unlikely"),
}


@dataclass(frozen=True)
class AttendanceRule:
    """One row of the wynnvets.org attendance-priority table. ``memberships``
    is the set this row applies to; ``core`` None = applies to both Core and
    Fill. Rules are evaluated top-to-bottom; first match wins (see
    ``app.domain.attendance``)."""

    memberships: frozenset[MembershipTier]
    core: bool | None          # True=Non-Fill only, False=Fill only, None=either
    notice: AttendanceNotice
    likelihood: Likelihood


_GUILD_WL = frozenset({MembershipTier.MEMBER, MembershipTier.WAITLIST,
                       MembershipTier.HONOURARY})
_MEMBER = frozenset({MembershipTier.MEMBER})
_COMMUNITY = frozenset({MembershipTier.COMMUNITY})
_ALLY = frozenset({MembershipTier.ALLY})
_OTHER = frozenset({MembershipTier.OTHER})

#: Ordered exactly as the published table (top = highest priority).
ATTENDANCE_TABLE: tuple[AttendanceRule, ...] = (
    AttendanceRule(_MEMBER, None, AttendanceNotice.ONE_HR_EARLY,
                   Likelihood.VIRTUALLY_GUARANTEED),
    AttendanceRule(_GUILD_WL, True, AttendanceNotice.HARD_RSVP,
                   Likelihood.VIRTUALLY_GUARANTEED),
    AttendanceRule(_GUILD_WL, False, AttendanceNotice.HARD_RSVP,
                   Likelihood.MORE_OFTEN_THAN_NOT),
    AttendanceRule(_GUILD_WL, True, AttendanceNotice.SOFT_RSVP,
                   Likelihood.MORE_OFTEN_THAN_NOT),
    AttendanceRule(_GUILD_WL, False, AttendanceNotice.SOFT_RSVP,
                   Likelihood.FREQUENTLY),
    AttendanceRule(_COMMUNITY, True, AttendanceNotice.HARD_RSVP,
                   Likelihood.FREQUENTLY),
    AttendanceRule(_COMMUNITY, False, AttendanceNotice.HARD_RSVP,
                   Likelihood.OFTEN),
    AttendanceRule(_COMMUNITY, True, AttendanceNotice.SOFT_RSVP,
                   Likelihood.OFTEN),
    AttendanceRule(_ALLY, True, AttendanceNotice.HARD_RSVP, Likelihood.OFTEN),
    AttendanceRule(_ALLY, True, AttendanceNotice.SOFT_RSVP, Likelihood.SOMETIMES),
    AttendanceRule(_COMMUNITY, False, AttendanceNotice.SOFT_RSVP,
                   Likelihood.SOMETIMES),
    AttendanceRule(_ALLY, False, AttendanceNotice.SOFT_RSVP, Likelihood.RARELY),
    AttendanceRule(_OTHER, True, AttendanceNotice.HARD_RSVP, Likelihood.RARELY),
)


# ---------------------------------------------------------------------------
# Role guidance (quoted/condensed from the docs; shown in the add-capability UI)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoleGuidance:
    title: str
    purpose: str
    requirements: str
    gameplay_url: str
    builds_url: str
    weapon_subtypes: tuple[str, ...] = field(default_factory=tuple)


ROLE_GUIDANCE: dict[Role, RoleGuidance] = {
    Role.PRIMARY: RoleGuidance(
        "Primary DPS (Boss Killer)",
        "The player tasked with melting the boss itself.",
        "450k+ close-range DPS. You stand behind the tank, fueled by a healer, "
        "and deal continuous damage to Anni's hitbox.",
        f"{DOCS_BASE}/#primary-dps", f"{DOCS_BASE}/#primary-builds",
    ),
    Role.SECONDARY: RoleGuidance(
        "Secondary DPS (Sun Killer)",
        "Players tasked with killing the floating orb that instakills everyone.",
        "200k+ ranged DPS. The target floats ~10 blocks up and explodes in ~10s "
        "if not killed; otherwise you do crowd control around the core.",
        f"{DOCS_BASE}/#secondary-dps", f"{DOCS_BASE}/#secondary-builds",
    ),
    Role.TERTIARY: RoleGuidance(
        "Tertiary DPS (Healing-Mob Killer)",
        "Players who eliminate the mobs that regenerate the boss' health.",
        "100k+ mobile DPS and reliable movement to cross a 15+ block lava pit. "
        "Mobs are low-HP but spawn in inconvenient places.",
        f"{DOCS_BASE}/#tertiary-dps", f"{DOCS_BASE}/#tertiary-builds",
    ),
    Role.HEALER: RoleGuidance(
        "Healer (Party Healer)",
        "Spam heals to keep the core alive. You are protected by the core.",
        "40k+ HPS. You only need to heal players in the core (~5 blocks wide) "
        "and rarely leave its vicinity.",
        f"{DOCS_BASE}/#healer", f"{DOCS_BASE}/#healer-builds",
    ),
    Role.TANK: RoleGuidance(
        "Tank (Party Tank)",
        "Protect everyone from Anni's wrath; stand in the boss' face.",
        "100k+ EHP with 15k+ HPR. You absorb all direct damage and never "
        "retreat. Paladin (aggro draw, Heavenly Trumpet) is ideal.",
        f"{DOCS_BASE}/#tank", f"{DOCS_BASE}/#tank-builds",
    ),
    Role.FILL: RoleGuidance(
        "Fill (Flexible Learner)",
        "Learn the mechanics with no requirements; rewards even if you die. "
        "Capacity is limited (≤30–40% of a party).",
        "None! Any build. Optionally help the tertiary DPS.",
        f"{DOCS_BASE}/#attending", f"{DOCS_BASE}/#attending",
    ),
}

#: Wynncraft item-search subtypes that count as weapons (for the catalog).
WEAPON_SUBTYPES: tuple[str, ...] = ("bow", "spear", "wand", "dagger", "relik")

#: A user may list MULTIPLE weapons per role capability (e.g. a primary-capable
#: user on both ``Labyrinth`` and ``Revolution``). Rules: (1) every weapon must
#: be real — validated against the cached WAPI item catalog at write time;
#: (2) at most this many weapons per (player, role) capability. The cap is
#: per-role: 3 for primary AND a separate 3 for secondary is fine.
MAX_WEAPONS_PER_CAPABILITY = 3

#: Epoch-0 sentinel: a WAPI ``lastJoin`` at/just-after the unix epoch means the
#: player has disabled their Wynncraft API (never appears online). Same
#: convention as dazebot's ``is_last_online_unknown`` (<= epoch + 1 day).
API_DISABLED_LAST_ONLINE_MAX = 86_400  # seconds past epoch
