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

    PRIMARY = "primary"      # Handles damaging the boss
    SECONDARY = "secondary"  # Handles damaging the sun mob
    TERTIARY = "tertiary"    # Clears out mobs that heal the boss
    HEALER = "healer"        # Heals the party's members
    TANK = "tank"            # Protects the party from the boss' damage
    FILL = "fill"            # No indicated roles.


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
    guildless; ALLY = guild tag in the configured ally-tag list
    (``settings.ally_guild_tags``); OTHER = any other guild.
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
    """How much warning we have that a user intends to attend.

    RSVP_HARD/RSVP_SOFT come from ``/rsvp`` and are the only values ever
    *stored* (on ``Rsvp.notice``). ATTEND_EARLY/ATTEND_LATE are *derived* and
    never stored: a no-RSVP user counts as early if they are online — or, on
    their own dashboard, *would* be online — at least
    ``EARLY_NOTICE_CUTOFF_SECONDS`` before the anni, else late."""

    ATTEND_EARLY = "attend_early"  # online/projected ≥cutoff before anni, no RSVP
    RSVP_HARD = "rsvp_hard"        # hard RSVP via /rsvp (stored on Rsvp.notice)
    RSVP_SOFT = "rsvp_soft"        # soft RSVP via /rsvp (stored on Rsvp.notice)
    ATTEND_LATE = "attend_late"    # online/projected within the late window, no RSVP


#: Boundary between ATTEND_EARLY and ATTEND_LATE. A no-RSVP user who is (or,
#: on their dashboard, projects to be) online at least this long before the
#: anni counts as "1 hr early"; closer than this is "late".
EARLY_NOTICE_CUTOFF_SECONDS = 3600


class PresenceStatus(StrEnum):
    """How we see a user *right now* for the active anni."""

    OFFLINE_GONE = "offline_gone"          # was here <=60 mins before, now offline.
    OFFLINE_HARD = "offline_hard"          # offline, hard RSVP
    OFFLINE_SOFT = "offline_soft"          # offline, soft RSVP
    ONLINE_ELSEWHERE = "online_elsewhere"  # online, wrong world
    ONLINE_WORLD = "online_world"          # online, party world, not in party
    ONLINE_PARTY = "online_party"          # online, in the party
    UNKNOWN = "unknown"                    # API disabled, not confident in world-change workaround or vetsmod-workaround guesses.


#: Anni has big queues, and players in queues are connecting: not gone.
#  Players in the online-merge source report as ``queued`` and should be considered ONLINE_ELSEWHERE.
QUEUE_NEVER_OFFLINE_GONE = True


class ConfidenceLevel(StrEnum):
    """Self-assessed confidence/preference for a role (and reused for build
    quality). HIGH = confident & enjoys it; LOW = inexperienced/dispreferred."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


# Build quality uses the same three levels but is a distinct concept.
BuildQuality = ConfidenceLevel


class ContinentCode(StrEnum):
    """A user's preferred play region(s) — the canonical **MaxMind GeoIP2
    continent codes** (https://dev.maxmind.com/geoip — the ``continent.code``
    field). All seven are included verbatim so the vocabulary stays faithful
    to MaxMind even though AN is, in practice, never picked.

    Stored on ``AnniPlayer.preferred_regions`` as a CSV of these codes
    (human-readable, multi-value, migration-light — same rationale as the
    string-stored enums above). Parsed/formatted by ``app.domain.regions``."""

    AF = "AF"  # Africa
    AN = "AN"  # Antarctica
    AS = "AS"  # Asia
    EU = "EU"  # Europe
    NA = "NA"  # North America
    OC = "OC"  # Oceania
    SA = "SA"  # South America


#: Full continent name for each code (the picker label + the pill ``title``).
CONTINENT_LABEL: dict[ContinentCode, str] = {
    ContinentCode.AF: "Africa",
    ContinentCode.AN: "Antarctica",
    ContinentCode.AS: "Asia",
    ContinentCode.EU: "Europe",
    ContinentCode.NA: "North America",
    ContinentCode.OC: "Oceania",
    ContinentCode.SA: "South America",
}

#: Per-continent globe glyph (the user's chosen emoji). Earth faces roughly
#: the continent: 🌍 Africa/Europe, 🌎 the Americas, 🌏 Asia/Oceania; 🇦🇶 for
#: Antarctica. Paired in the pill with the code text + full-name title, so
#: colour/glyph is never the only signal (the colourblind hard-rule).
CONTINENT_GLYPH: dict[ContinentCode, str] = {
    ContinentCode.AF: "🌍",
    ContinentCode.EU: "🌍",
    ContinentCode.NA: "🌎",
    ContinentCode.SA: "🌎",
    ContinentCode.AS: "🌏",
    ContinentCode.OC: "🌏",
    ContinentCode.AN: "🇦🇶",
}

#: Glyph for the "no preference / any region" pill (a generic globe, distinct
#: from the continent faces above so it doesn't read as a specific region).
ANY_REGION_GLYPH = "🌐"

#: Regions Wynn currently runs server proxies for — the picker's default
#: offer set. Wynn has only implemented AS/EU/NA so far; offering the others
#: would just confuse users. They stay in the vocabulary (so a stored value
#: still parses + displays) and the offer set is widened via the
#: ``ENABLED_REGIONS`` setting as Wynn adds proxies — see ``app.settings`` /
#: ``app.domain.regions``.
DEFAULT_ENABLED_REGIONS: tuple[ContinentCode, ...] = (
    ContinentCode.AS,
    ContinentCode.EU,
    ContinentCode.NA,
)

#: Canonical display/storage order (MaxMind documents the codes alphabetically;
#: parse/format normalise to this so the stored CSV is order-stable).
CONTINENT_ORDER: tuple[ContinentCode, ...] = (
    ContinentCode.AF,
    ContinentCode.AN,
    ContinentCode.AS,
    ContinentCode.EU,
    ContinentCode.NA,
    ContinentCode.OC,
    ContinentCode.SA,
)


class BucketKind(StrEnum):
    """Non-party containers on the organizer board."""

    UNASSIGNED = "unassigned"   # rsvp'd / 1hr-early, not placed. Sub-bucket for late users.
    WONTASSIGN = "wontassign"   # here, but confirmed intention to sit this one out.
    VOLUNTEERS = "volunteers"   # here, but confirmed willingness to sit out *if absolutely needed*.


#: User-facing bucket label (the raw enum values read badly in the UI).
BUCKET_LABEL: dict[BucketKind, str] = {
    BucketKind.UNASSIGNED: "Unassigned",
    BucketKind.WONTASSIGN: "Sitting out",
    BucketKind.VOLUNTEERS: "Volunteering (will sit out if needed)",
}


class PartyResult(StrEnum):
    TBD = "tbd" # This party is either about to fight, or is still fighting, anni. We don't know the result yet.
    LOSS = "loss" # This party lost.
    LAG = "lag" # This party's experience was so broken that, despite technically being a loss, we refuse to count it as such.
    WIN = "win" # This party won.


#: Party stage 1..5 with the organiser-facing label (spec "Stages").
PARTY_STAGE_LABELS: dict[int, str] = {
    1: "The organiser hasn't started.",
    2: "Determining how many parties we can support.",
    3: "Determining which core users go in which parties.",
    4: "Finalising everyone's roles.",
    5: "Parties finalised — fill slots added/ready.",
}
MIN_PARTY_STAGE, MAX_PARTY_STAGE = 1, 5
PARTY_CAPACITY = 10


# ---------------------------------------------------------------------------
# Colour palettes + non-colour encodings
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Style:
    """One palette entry — the four colour channels every chip uses. Defined
    ONCE in ``STYLES`` and shared by both ROLE_STYLES and STATUS_STYLES so a
    colour has a single source of truth. Mirrored by name in
    static/css/anni.css (``--c-*``) and swapped under ``body.cb`` by
    static/css/colourblind.css. ``light``/``dark`` are hand-tuned for legible
    text — they are NOT a uniform tint/shade; don't regenerate them."""

    color: str   # default palette (hex)
    light: str   # tint — legible surface for BLACK text
    dark: str    # shade — legible surface for WHITE text
    cb: str       # colourblind-safe (canonical Okabe-Ito); body.cb swaps to this


class PaletteColor(StrEnum):
    """The shared colour vocabulary. Each role is paired with exactly one
    status on the same entry (see ROLE_STYLES / STATUS_STYLES); GREY is the
    neutral for 'no role' / 'unknown'."""

    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    CYAN = "cyan"
    MAGENTA = "magenta"
    GREY = "grey"


#: THE single source of truth for every chip colour. Keep in lockstep with
#: static/css/anni.css (:root ``--c-*``) and colourblind.css (``body.cb``).
#: Values are hand-tuned for legibility — do not "uniformly" regenerate them.
STYLES: dict[PaletteColor, Style] = {
    PaletteColor.RED:    Style("#ff0000", "#ff9292", "#990000", "#D55E00"),
    PaletteColor.YELLOW: Style("#fffb00", "#f5f36e", "#696800", "#F0E442"),
    PaletteColor.GREEN:  Style("#15ff00", "#83ff78", "#0a7700", "#009E73"),
    PaletteColor.BLUE:   Style("#0400ff", "#9290ff", "#0300AC", "#0072B2"),
    PaletteColor.CYAN: Style("#00e1ff", "#4aeaff", "#007E8F", "#56B4E9"),
    PaletteColor.MAGENTA: Style("#ff00dd", "#ff7aff", "#6000b9", "#CC79A7"),
    PaletteColor.GREY:   Style("#888888", "#d6d6d6", "#3d3d3d", "#999999"),
}


@dataclass(frozen=True)
class RoleStyle:
    """A role *background* chip: a shared ``STYLES`` colour spread flat (so
    templates keep ``.color``/``.cb`` access) + the role's ``glyph``/``label``
    which make it readable without colour."""

    color: str        # default palette (hex)
    light: str        # tint — surface for BLACK text
    dark: str         # shade — surface for WHITE text
    cb: str           # colourblind-safe palette (canonical Okabe-Ito)
    glyph: str        # short code shown on the pill/person object
    label: str        # accessible label (aria)


@dataclass(frozen=True)
class StatusStyle:
    """A person's *status border* chip. Same four shared colour channels as
    RoleStyle, plus ``pattern`` — a non-colour channel — and ``glyph`` +
    ``label`` for the colourblind variant and screen readers.

    ``pattern`` encodes online-ness by family so the border alone reads
    online vs offline. ONLINE = unbroken, escalating with presence:
    ``solid`` (elsewhere) → ``double`` (world) → ``triple`` (in party).
    OFFLINE = broken, degrading with risk: ``long-dash`` (hard RSVP) →
    ``short-dash`` (soft RSVP) → ``dotted`` (gone). ``wavy`` = UNKNOWN
    (neither — unconfirmable). Rendered by static/css/colourblind.css
    ``.status-border[data-pattern=…]``."""

    color: str
    light: str
    dark: str
    cb: str
    pattern: str
    glyph: str
    label: str


def _role(s: Style, glyph: str, label: str) -> RoleStyle:
    return RoleStyle(s.color, s.light, s.dark, s.cb, glyph, label)


def _status(s: Style, pattern: str, glyph: str, label: str) -> StatusStyle:
    return StatusStyle(s.color, s.light, s.dark, s.cb, pattern, glyph, label)


# Role → shared colour (spec.md [^5]). A role and its paired status share the
# SAME STYLES entry — see STATUS_STYLES.
ROLE_STYLES: dict[Role, RoleStyle] = {
    Role.PRIMARY:   _role(STYLES[PaletteColor.RED],    "PRIM", "Primary (boss killer)"),
    Role.SECONDARY: _role(STYLES[PaletteColor.YELLOW], "SUNK", "Secondary (sun killer)"),
    Role.TERTIARY:  _role(STYLES[PaletteColor.MAGENTA], "HDMG", "Tertiary (mob killer)"),
    Role.HEALER:    _role(STYLES[PaletteColor.GREEN],  "HEAL", "Healer"),
    Role.TANK:      _role(STYLES[PaletteColor.BLUE],   "TANK", "Tank"),
    Role.FILL:      _role(STYLES[PaletteColor.CYAN], "FILL", "Fill"),
}
#: Unassigned person object background (no role yet).
UNASSIGNED_STYLE = _role(STYLES[PaletteColor.GREY], "—", "Unassigned")


# Status border → the SAME shared colour as its paired role (spec.md [^6]):
# GONE↔PRIMARY(red), SOFT↔SECONDARY(yellow), HARD↔HEALER(green),
# ELSEWHERE↔TANK(blue), WORLD↔FILL(cyan), PARTY↔TERTIARY(magenta).
STATUS_STYLES: dict[PresenceStatus, StatusStyle] = {
    PresenceStatus.OFFLINE_GONE: _status(
        STYLES[PaletteColor.RED], "dotted", "!",
        "A user who was here but has since logged out"),
    PresenceStatus.OFFLINE_HARD: _status(
        STYLES[PaletteColor.GREEN], "long-dash", "✓",
        "A hard RSVP'd user who is not here yet"),
    PresenceStatus.OFFLINE_SOFT: _status(
        STYLES[PaletteColor.YELLOW], "short-dash", "~",
        "A soft RSVP'd user who is not here yet"),
    PresenceStatus.ONLINE_ELSEWHERE: _status(
        STYLES[PaletteColor.BLUE], "solid", "→",
        "An online user not on their party's world"),
    PresenceStatus.ONLINE_WORLD: _status(
        STYLES[PaletteColor.CYAN], "double", "◐",
        "An on-world online user who has not joined their party yet."),
    PresenceStatus.ONLINE_PARTY: _status(
        STYLES[PaletteColor.MAGENTA], "triple", "●",
        "An online user who has joined their party."),
    PresenceStatus.UNKNOWN: _status(
        STYLES[PaletteColor.GREY], "wavy", "?",
        "Unknown — API disabled / unconfirmable"),
}


# ---------------------------------------------------------------------------
# Attendance likelihood (the bottom bar on the dashboard)
# ---------------------------------------------------------------------------
# The published table maps (membership, Core/Fill, notice) to an *exact
# percentage*. Users must never see that number (spec: an exact probability
# invites rules-lawyering) — ``app.domain.attendance.meta`` collapses it into
# one of the visible bands below and only the band LABEL is ever rendered.
#
# Banding (the published "Visible Sort Orders"): a percentage maps to the
# FIRST band whose exclusive upper bound it is below. Band index is 1..6
# (worst -> best). An off-table cell (an N/A cell, or a non-trackable tier
# with no RSVP) is treated as 0% — i.e. still "Most Unlikely"; there is no
# distinct "not prioritised" level.
LIKELIHOOD_BANDS: tuple[tuple[int, str], ...] = (
    (1,   "Most Unlikely"),    # < 1%
    (20,  "Very Unlikely"),    # < 20%
    (40,  "Unlikely"),         # < 40%
    (60,  "Likely"),           # < 60%
    (80,  "Very Likely"),      # < 80%
    (100, "Most Likely"),      # < 100%
)


@dataclass(frozen=True)
class AttendanceRule:
    """One row of the wynnvets.org attendance-priority table. ``memberships``
    is the tier(s) this row applies to; ``core`` True=Non-Fill, False=Fill,
    None=either. ``pct`` is the raw attendance probability for this cell —
    internal only, never shown to the user (see ``LIKELIHOOD_BANDS``). Rules
    are evaluated top-to-bottom, first match wins (see
    ``app.domain.attendance``); an N/A cell is simply absent (no rule)."""

    memberships: frozenset[MembershipTier]
    core: bool | None          # True=Non-Fill only, False=Fill only, None=either
    notice: AttendanceNotice
    pct: int                   # raw probability for this cell (0-100); never shown


_MEMBER = frozenset({MembershipTier.MEMBER})
_WAITLIST = frozenset({MembershipTier.WAITLIST})
_HONOURARY = frozenset({MembershipTier.HONOURARY})
_COMMUNITY = frozenset({MembershipTier.COMMUNITY})
_ALLY = frozenset({MembershipTier.ALLY})
_OTHER = frozenset({MembershipTier.OTHER})

_E = AttendanceNotice.ATTEND_EARLY   # ">1hr Early" column
_H = AttendanceNotice.RSVP_HARD      # "Hard RSVP" column
_S = AttendanceNotice.RSVP_SOFT      # "Soft RSVP" column
_L = AttendanceNotice.ATTEND_LATE    # "Late" column

#: Ordered exactly as the published table (top = highest priority). N/A cells
#: (Community/Ally/Other × Early/Late) have no row — evaluate() returns None.
ATTENDANCE_TABLE: tuple[AttendanceRule, ...] = (
    AttendanceRule(_MEMBER,    True,  _E, 90),
    AttendanceRule(_MEMBER,    True,  _H, 80),
    AttendanceRule(_MEMBER,    True,  _S, 50),
    AttendanceRule(_MEMBER,    True,  _L, 20),
    AttendanceRule(_MEMBER,    False, _E, 80),
    AttendanceRule(_MEMBER,    False, _H, 65),
    AttendanceRule(_MEMBER,    False, _S, 30),
    AttendanceRule(_MEMBER,    False, _L, 10),
    AttendanceRule(_WAITLIST,  True,  _E, 81),
    AttendanceRule(_WAITLIST,  True,  _H, 71),
    AttendanceRule(_WAITLIST,  True,  _S, 41),
    AttendanceRule(_WAITLIST,  True,  _L, 16),
    AttendanceRule(_WAITLIST,  False, _E, 61),
    AttendanceRule(_WAITLIST,  False, _H, 41),
    AttendanceRule(_WAITLIST,  False, _S, 21),
    AttendanceRule(_WAITLIST,  False, _L, 6),
    AttendanceRule(_HONOURARY, True,  _E, 80),
    AttendanceRule(_HONOURARY, True,  _H, 70),
    AttendanceRule(_HONOURARY, True,  _S, 40),
    AttendanceRule(_HONOURARY, True,  _L, 15),
    AttendanceRule(_HONOURARY, False, _E, 60),
    AttendanceRule(_HONOURARY, False, _H, 40),
    AttendanceRule(_HONOURARY, False, _S, 20),
    AttendanceRule(_HONOURARY, False, _L, 5),
    AttendanceRule(_COMMUNITY, True,  _H, 30),
    AttendanceRule(_COMMUNITY, True,  _S, 5),
    AttendanceRule(_COMMUNITY, False, _H, 20),
    AttendanceRule(_COMMUNITY, False, _S, 0),
    AttendanceRule(_ALLY,      True,  _H, 20),
    AttendanceRule(_ALLY,      True,  _S, 5),
    AttendanceRule(_ALLY,      False, _H, 10),
    AttendanceRule(_ALLY,      False, _S, 0),
    AttendanceRule(_OTHER,     True,  _H, 5),
    AttendanceRule(_OTHER,     True,  _S, 0),
    AttendanceRule(_OTHER,     False, _H, 5),
    AttendanceRule(_OTHER,     False, _S, 0),
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
        "100k+ DPS and reliable movement to cross a 15+ block lava pit. "
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
