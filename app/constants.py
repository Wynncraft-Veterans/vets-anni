"""Domain enums + data tables.

This module is the single source of truth for the *vocabulary* of the system:
roles, membership tiers, presence statuses, the colour palettes (default +
colourblind-safe), the attendance-priority table, and the role guidance text.

Pure data only — no DB, FastAPI, or discord imports — so the domain layer and
tests can use it freely. Colours are NEVER the only signal: every role/status
also carries a short glyph + label + (for statuses) a traffic-light lamp
pattern so the colourblind variant is fully usable (spec hard requirement).

Sources:
* Role/status colours — see .claude/spec.md ([^5]/[^6]) + concept-art legend.
* Attendance table & role guidance — https://www.wynnvets.org/docs/guild/anni/
"""

from __future__ import annotations

import re
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

#: How close to the anni an ARRIVAL counts as late, per RSVP state — the
#: hourglass on the person card. Sliding rather than one cutoff, because how
#: much slack someone has earned depends on what they promised: a hard RSVP
#: is expected and only reads late in the last quarter-hour, while a walk-in
#: nobody was told about is late from T-50.
#:
#: ``REVOKED`` maps to ``None`` = never late. A retraction is not a
#: late arrival, it is an absence, and one that already moves the card to
#: Sitting-out on its own (``buckets.demote_on_revoke``).
#:
#: Evaluated ONCE, when the card lands on the board (``buckets.ensure_placed``
#: / ``add_walkin``) — it is a fact about when someone turned up, so it is
#: stored on ``BoardPlacement.is_late`` rather than recomputed. Keyed by
#: ``RsvpState``; the table lives here so ``services/hot_window`` stays pure.
LATE_ARRIVAL_SECONDS: dict[str, int | None] = {
    "none": 50 * 60,     # walk-in — nobody was expecting them
    "soft": 35 * 60,
    "hard": 15 * 60,     # promised, so the most slack
    "revoked": None,     # n/a
}

#: How long before the anni new ``/rsvp`` declarations stop being accepted.
#: Closer than this, ``\rsvp hard`` / ``\rsvp soft`` are refused (including
#: upgrades/downgrades of an existing RSVP); the user is told to show up an
#: hour early as a walk-in or take their chances by joining late. Revokes are
#: never blocked. The staff override ``\rsvp set`` bypasses this gate.
RSVP_CUTOFF_SECONDS = 90 * 60


class PresenceStatus(StrEnum):
    """How we see a user *right now* for the active anni.

    Presence ONLY, and only *right now*. Two things that used to live here
    have been split off into their own channels on the person card, because
    each is a fact the border kept hiding:

    * the RSVP (once ``OFFLINE_HARD``/``OFFLINE_SOFT``) → :class:`RsvpState`,
      the ticket left of the avatar;
    * "was here earlier and left" (once ``OFFLINE_GONE``) → a badge stamped
      on the avatar, alongside the late-arrival hourglass. It is history, not
      presence: someone gone is simply ``OFFLINE``, and folding the two into
      one border meant an organiser could not see *both* "not here" and "and
      they were, ten minutes ago" at once.

    So the border answers exactly one question — where are they right now.
    Declared most→least "present" (the legend and the CB traffic light both
    read this order).
    """

    ONLINE_PARTY = "online_party"          # online, party world, in the party
    ONLINE_WORLD = "online_world"          # online, party world, not in party
    ONLINE_ELSEWHERE = "online_elsewhere"  # online, wrong/unknown world or queued
    OFFLINE = "offline"                    # not here right now
    UNKNOWN = "unknown"                    # API disabled, not confident in world-change workaround or vetsmod-workaround guesses.


class RsvpState(StrEnum):
    """The RSVP axis, as the board renders it — the second, independent
    channel next to :class:`PresenceStatus`.

    Derived per (event, player) from :class:`app.db.models.Rsvp`: no row at
    all is ``NONE`` (a walk-in — they never declared), a live row is
    ``HARD``/``SOFT`` per its stored notice, and a soft-deleted one
    (``revoked_at`` set) is ``REVOKED``. ``REVOKED`` deliberately outranks the
    stored notice: "they pulled out" is the fact an organiser needs, not what
    they had said before pulling out.
    """

    NONE = "none"        # never RSVP'd — a walk-in
    HARD = "hard"        # live Rsvp row, notice=RSVP_HARD
    SOFT = "soft"        # live Rsvp row, notice=RSVP_SOFT
    REVOKED = "revoked"  # Rsvp row soft-deleted (revoked_at set)


#: Anni has big queues, and players in queues are connecting: not absent.
#  Players in the online-merge source report as ``queued`` and should be
#  considered ONLINE_ELSEWHERE.
QUEUE_IS_NEVER_OFFLINE = True


class ConfidenceLevel(StrEnum):
    """Self-assessed confidence/preference for a role (and reused for
    reliability). HIGH = confident & enjoys it; LOW = inexperienced/dispreferred."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


# Reliability uses the same three levels but is derived, never self-declared
# — see ``app.domain.reliability``.
Reliability = ConfidenceLevel


class SetbackKind(StrEnum):
    """A recorded bad outcome that can cost reliability (``models.Setback``)."""

    LOSS = "loss"      # sat in a party, in this role, that lost
    MISSED = "missed"  # hard-RSVP'd and never turned up


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


#: WAPI v3 guild member rank keys treated as "staff" — the lead-organiser
#: candidate set (ALL of them, online or not; ``services/online_merge`` caches
#: it from the guild payload it already fetches). The conventional Wynncraft
#: management ranks; widen/narrow per guild via ``STAFF_GUILD_RANKS`` env (see
#: ``app.settings``). Matched case-insensitively (WAPI keys are lower-case:
#: owner/chief/strategist/recruiter/recruit). Captain was retired in the
#: 2026-07 permission restructure; if a stray Captain re-appears they are
#: treated as a non-staff Returner.
DEFAULT_STAFF_GUILD_RANKS: tuple[str, ...] = (
    "owner", "chief", "strategist",
)


class BucketKind(StrEnum):
    """Non-party containers on the organizer board."""

    UNASSIGNED = "unassigned"   # not placed. Three sub-buckets: main (RSVP'd), walk-in (auto-detected non-RSVP, T-70..T-60), late (placed after T-60).
    WONTASSIGN = "wontassign"   # here, but confirmed intention to sit this one out.
    VOLUNTEERS = "volunteers"   # here, but confirmed willingness to sit out *if absolutely needed*.


#: User-facing bucket label (the raw enum values read badly in the UI).
BUCKET_LABEL: dict[BucketKind, str] = {
    BucketKind.UNASSIGNED: "Unassigned",
    BucketKind.WONTASSIGN: "Sitting out",
    BucketKind.VOLUNTEERS: "Volunteering (will sit out if needed)",
}


class PartyResult(StrEnum):
    # Still TBD when the grace-wipe runs = staff never recorded a result. The
    # wipe treats that as WIN (see ``services/lifecycle_task._credit_wins``):
    # staff had the whole 2h grace window to mark ``LOSS``/``LAG`` and didn't,
    # so players who were assigned a core role still get success_count credit.
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
    ONCE in ``STYLES`` and shared by ROLE_STYLES, STATUS_STYLES and
    RSVP_STYLES so a colour has a single source of truth. Mirrored by name in
    static/css/anni.css (``--c-*``) and swapped under ``body.cb`` by
    static/css/colourblind.css. ``light``/``dark`` are hand-tuned for legible
    text — they are NOT a uniform tint/shade; don't regenerate them."""

    color: str   # default palette (hex)
    light: str   # tint — legible surface for BLACK text
    dark: str    # shade — legible surface for WHITE text
    cb: str       # colourblind-safe (canonical Okabe-Ito); body.cb swaps to this


class PaletteColor(StrEnum):
    """The shared colour vocabulary — two tonal families plus a neutral.

    Roles, statuses and RSVP tickets all draw from this one set so a hue has
    a single source of truth (and one CB swap), split two ways:

    * the **neon family** (RED/YELLOW/GREEN/BLUE/CYAN/MAGENTA) is the role
      palette — max-saturation hues that read as chip fills and glyph
      swatches — and the **status borders** re-use it;
    * the **mid-tone family** (TEAL/LIME/BRICK/ORCHID) is the RSVP ticket.

    Statuses re-use the hue of the role each one evokes: in-party is FILL's
    aqua, on-world is HEALER's green, elsewhere is SECONDARY's sun-gold,
    offline is PRIMARY's red. That is a mnemonic, not a coincidence — the
    board is already dense, and a status hue an organiser has to learn
    separately is one more thing to learn. A role and a status sharing a hue
    is safe because they never share a channel: a role is a card background
    or a glyph swatch, a status is the border around the whole card.

    (This inverts an earlier arrangement where statuses had the mid-tone set
    to themselves precisely so they could NOT be confused with roles. What
    changed is the judgement, not the constraint: a deliberate echo of the
    role palette teaches faster than an unrelated one avoids confusion.)

    What must stay disjoint is the RSVP ticket, which sits ON the card as its
    own object rather than colouring part of it — hence its own family. GREY
    is the neutral both borrow for "no role" / "unknown".
    """

    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    CYAN = "cyan"
    MAGENTA = "magenta"
    TEAL = "teal"
    LIME = "lime"
    BRICK = "brick"
    ORCHID = "orchid"
    GREY = "grey"


#: THE single source of truth for every chip colour. Keep in lockstep with
#: static/css/anni.css (:root ``--c-*``) and colourblind.css (``body.cb``).
#: Values are hand-tuned for legibility — do not "uniformly" regenerate them.
STYLES: dict[PaletteColor, Style] = {
    # --- neon family: roles + the RSVP icons ------------------------------
    PaletteColor.RED:    Style("#ff0000", "#ff9292", "#990000", "#D55E00"),
    PaletteColor.YELLOW: Style("#fffb00", "#f5f36e", "#696800", "#F0E442"),
    PaletteColor.GREEN:  Style("#15ff00", "#83ff78", "#0a7700", "#009E73"),
    PaletteColor.BLUE:   Style("#0400ff", "#9290ff", "#0300AC", "#0072B2"),
    # CB hue is Okabe-Ito BLACK: every other entry in the set is spoken for.
    # Still fine now that CYAN also backs the ONLINE_PARTY border, because
    # under cb that border is drawn OUTSIDE the card (colourblind.css) — i.e.
    # against the pale dropzone, where black is the highest-contrast ring
    # available rather than an invisible one.
    PaletteColor.CYAN: Style("#00e1ff", "#4aeaff", "#007E8F", "#000000"),
    PaletteColor.MAGENTA: Style("#ff00dd", "#ff7aff", "#6000b9", "#CC79A7"),
    # --- mid-tone family: the status ramp, in ramp order ------------------
    # Hand-picked as a set (not derived from the neon hues): cyan → green →
    # yellow-green → red → orchid walks the wheel in one direction, so the
    # borders read as a scale. LIME is the "yellow between orange and green"
    # step — a true yellow-green, which is what sits between them on the
    # wheel. CB hues walk the same direction through Okabe-Ito.
    PaletteColor.TEAL:   Style("#37c5c8", "#a5e6e7", "#16686a", "#56B4E9"),
    PaletteColor.LIME:   Style("#93bd42", "#d3e5a8", "#4d6420", "#F0E442"),
    PaletteColor.BRICK:  Style("#c93e36", "#eeb0ac", "#6b201c", "#D55E00"),
    PaletteColor.ORCHID: Style("#c13eab", "#e9aede", "#66205a", "#CC79A7"),
    PaletteColor.GREY:   Style("#888888", "#d6d6d6", "#3d3d3d", "#999999"),
}

# CB-hue reuse is deliberate and safe. TEAL/LIME/BRICK/ORCHID land on
# sky-blue / yellow / vermillion / reddish-purple, some of which a role or a
# status also uses; statuses share the role hues outright, on purpose. Nothing
# has to be told apart across those channels — a role is a background or a
# glyph swatch, a status is a border, an RSVP ticket is an object on the card
# — and every chip carries a glyph/label (+ lamps for statuses) anyway
# (colourblind.md).
# What matters is that the five STATUS hues are mutually distinct under CVD,
# and they are (black / bluish-green / yellow / vermillion / grey).


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
    code: str         # two-letter cb code: the capability pip text + its legend key
    label: str        # accessible label (aria)


@dataclass(frozen=True)
class StatusStyle:
    """A person's *status* chip. Same four shared colour channels as
    RoleStyle (the border colour, default mode), plus ``lamps`` — the
    colourblind variant's channel — and ``glyph`` + ``label`` for screen
    readers.

    ``label`` is a SHORT name ("In Party"), because it is read in two places
    that both want brevity: the legend, which is scanned rather than read,
    and every ``aria-label`` on a card — "status In Party" beats "status An
    online user who has joined their party." for anyone actually listening to
    it. ``description`` keeps the sentence, as the legend chip's hover title,
    so the explanation is still one hover away.

    ``lamps`` is a three-lamp traffic light down the card's left edge, one
    character per lamp top→bottom, ``1`` = lit (white) and ``0`` = dark.
    CB-only: under ``body.cb`` it REPLACES the status border, which it
    replaced because a border pattern (double/solid/dash/dash-dot/…) proved
    too confusing to read. The lit lamp climbs with presence — bottom =
    online elsewhere, middle = on the party's world, top = in the party —
    so position alone reads as "how close are they"; none lit is offline,
    and top + bottom (a shape no single step makes) is unknown.
    Rendered by ``status_lamps`` in templates/macros/chips.html."""

    color: str
    light: str
    dark: str
    cb: str
    lamps: str        # "100" … top→bottom, 1 = lit — the cb traffic light
    glyph: str
    label: str        # short name — the legend key, and every aria-label
    description: str  # the full sentence — the legend chip's hover title


def _role(s: Style, glyph: str, code: str, label: str) -> RoleStyle:
    return RoleStyle(s.color, s.light, s.dark, s.cb, glyph, code, label)


def _status(
    s: Style, lamps: str, glyph: str, label: str, description: str
) -> StatusStyle:
    return StatusStyle(
        s.color, s.light, s.dark, s.cb, lamps, glyph, label, description)


# Role → shared colour (spec.md [^5]). Roles draw from the same ``STYLES``
# table as statuses and RSVP flags, but each family now picks its own entries
# — there is no longer a role↔status pairing to keep in step.
#: ``code`` is the colourblind variant's text for a role — white text in a
#: black box, on the capability pips and on the legend key that decodes them.
#: Two letters from the glyph, so the two vocabularies read as one.
ROLE_STYLES: dict[Role, RoleStyle] = {
    Role.PRIMARY:   _role(STYLES[PaletteColor.RED],    "PRIM", "PR", "Primary (boss killer)"),
    Role.SECONDARY: _role(STYLES[PaletteColor.YELLOW], "SUNK", "SU", "Secondary (sun killer)"),
    Role.TERTIARY:  _role(STYLES[PaletteColor.MAGENTA], "HDMG", "HD", "Tertiary (mob killer)"),
    Role.HEALER:    _role(STYLES[PaletteColor.GREEN],  "HEAL", "HE", "Healer"),
    Role.TANK:      _role(STYLES[PaletteColor.BLUE],   "TANK", "TA", "Tank"),
    Role.FILL:      _role(STYLES[PaletteColor.CYAN], "FILL", "FI", "Fill"),
}
#: Unassigned person object background (no role yet).
UNASSIGNED_STYLE = _role(STYLES[PaletteColor.GREY], "—", "—", "Unassigned")


#: Default intra-container ordering by assigned role. Lower = sorts earlier.
#: ``None`` covers the "no assigned role yet" grey card and sorts last. Used
#: by ``domain/buckets`` to seed ``sort_index`` on auto-place and by the
#: normalise-sort_index migration; drag intents can still override the
#: resulting order because ``sort_index`` is the persisted key.
ROLE_SORT_PRIORITY: dict[Role | None, int] = {
    Role.PRIMARY: 0,
    Role.SECONDARY: 1,
    Role.TERTIARY: 2,
    Role.HEALER: 3,
    Role.TANK: 4,
    Role.FILL: 5,
    None: 6,
}


# Status border — "how close are they to being in position?", declared
# most→least present. Each hue is the ROLE hue of whatever that state evokes,
# as a mnemonic (see PaletteColor):
#
#   ONLINE_PARTY     CYAN   — in position          (FILL's aqua)
#   ONLINE_WORLD     GREEN  — right world, not in the party yet (HEALER)
#   ONLINE_ELSEWHERE YELLOW — online but somewhere else, or queued (SECONDARY)
#   OFFLINE          RED    — not here right now    (PRIMARY)
#   UNKNOWN          GREY   — API disabled / unconfirmable
#
# The glyph ramp mirrors it: ● full → ◐ half → → moving → ○ empty →
# ? unconfirmable, so the channel reads with no colour at all.
#
# "Was here and left" is deliberately NOT in this table any more. It is not a
# degree of presence, it is history — and as a sixth border it competed with
# the five that describe the present. It is a badge on the avatar instead.
STATUS_STYLES: dict[PresenceStatus, StatusStyle] = {
    PresenceStatus.ONLINE_PARTY: _status(
        STYLES[PaletteColor.CYAN], "100", "●", "In Party",
        "An online user who has joined their party."),
    PresenceStatus.ONLINE_WORLD: _status(
        STYLES[PaletteColor.GREEN], "010", "◐", "In World",
        "An on-world online user who has not joined their party yet."),
    PresenceStatus.ONLINE_ELSEWHERE: _status(
        STYLES[PaletteColor.YELLOW], "001", "→", "Online",
        "An online user not on their party's world."),
    PresenceStatus.OFFLINE: _status(
        STYLES[PaletteColor.RED], "000", "○", "Offline",
        "A user who is not online right now."),
    PresenceStatus.UNKNOWN: _status(
        STYLES[PaletteColor.GREY], "101", "?", "Unknown",
        "Their Wynncraft API is disabled and we cannot confirm either way."),
}


@dataclass(frozen=True)
class RsvpStyle:
    """The RSVP badge on the person card — the axis lifted *out* of the status
    border. Same four shared colour channels as the other two chip families,
    plus ``icon`` + ``label``.

    ``icon`` names a shape in ``templates/macros/icons.html`` (bare marks in
    the FontAwesome regular-slab idiom — no chip, no outline box, just the
    mark). All four are **tickets**; what carries the meaning is what is on
    each one, plus the outline style and the silhouette, all of which survive
    greyscale. Colour only reinforces."""

    color: str
    light: str
    dark: str
    cb: str
    icon: str
    label: str


def _rsvp(s: Style, icon: str, label: str) -> RsvpStyle:
    return RsvpStyle(s.color, s.light, s.dark, s.cb, icon, label)


#: RSVP badge → shared colour + icon + label. Every state is a **ticket**, so
#: what distinguishes them is what is on it: a tick (issued and confirmed), a
#: clock (issued, still pending), an exclamation on a dotted outline (no
#: ticket was ever issued), or nothing at all on one torn in two (issued,
#: then void). Deliberately NOT a traffic light — these are four categories,
#: not four degrees of the same thing, so the hues spread around the wheel
#: rather than walking a scale: green-blue, yellow-green, red-purple,
#: red-orange.
RSVP_STYLES: dict[RsvpState, RsvpStyle] = {
    RsvpState.NONE: _rsvp(
        STYLES[PaletteColor.LIME], "ticket-dashed", "No RSVP — walk-in"),
    RsvpState.HARD: _rsvp(
        STYLES[PaletteColor.TEAL], "ticket-check", "Hard RSVP"),
    RsvpState.SOFT: _rsvp(
        STYLES[PaletteColor.ORCHID], "ticket-clock", "Soft RSVP"),
    RsvpState.REVOKED: _rsvp(
        STYLES[PaletteColor.BRICK], "ticket-torn", "RSVP retracted"),
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
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


@dataclass(frozen=True)
class RoleGuidance:
    title: str
    purpose: str
    requirements: str
    gameplay_url: str
    builds_url: str
    weapon_subtypes: tuple[str, ...] = field(default_factory=tuple)
    #: The bar a player has to clear, verbatim from ``requirements`` — the UI
    #: bolds it so the number isn't lost in the prose around it.
    threshold: str = ""

    @property
    def requirement_parts(self) -> tuple[str, str, str, str]:
        """``requirements`` as ``(before, threshold, after, detail)``: the
        sentence carrying ``threshold`` split around it, then the prose after
        that sentence (the UI puts it on its own line). ``(requirements, "",
        "", "")`` if ``threshold`` isn't in there."""
        if not (self.threshold and self.threshold in self.requirements):
            return self.requirements, "", "", ""
        before, threshold, _ = self.requirements.partition(self.threshold)
        start = len(before) + len(threshold)
        # Search from the threshold's last char so "None!" ends its own
        # sentence; a terminator mid-token ("1.5k") isn't followed by space.
        m = _SENTENCE_END.search(self.requirements, start - 1)
        end = m.end() if m else len(self.requirements)
        return (before, threshold, self.requirements[start:end],
                self.requirements[end:].lstrip())


ROLE_GUIDANCE: dict[Role, RoleGuidance] = {
    Role.PRIMARY: RoleGuidance(
        "Primary DPS (Boss Killer)",
        "The player tasked with melting the boss itself.",
        "450k+ close-range DPS. You stand behind the tank, fueled by a healer, "
        "and deal continuous damage to Anni's hitbox.",
        f"{DOCS_BASE}/#primary-dps", f"{DOCS_BASE}/#primary-builds",
        threshold="450k+ close-range DPS",
    ),
    Role.SECONDARY: RoleGuidance(
        "Secondary DPS (Sun Killer)",
        "Players tasked with killing the floating orb that instakills everyone.",
        "200k+ ranged DPS. The target floats ~10 blocks up and explodes in ~10s "
        "if not killed; otherwise you do crowd control around the core.",
        f"{DOCS_BASE}/#secondary-dps", f"{DOCS_BASE}/#secondary-builds",
        threshold="200k+ ranged DPS",
    ),
    Role.TERTIARY: RoleGuidance(
        "Tertiary DPS (Healing-Mob Killer)",
        "Players who eliminate the mobs that regenerate the boss' health.",
        "150k+ DPS and reliable movement. Mobs are low-HP, but spawn in "
        "inconvenient places; you will need to cross a 15+ block lava pit.",
        f"{DOCS_BASE}/#tertiary-dps", f"{DOCS_BASE}/#tertiary-builds",
        threshold="150k+ DPS and reliable movement",
    ),
    Role.HEALER: RoleGuidance(
        "Healer (Party Healer)",
        "Spam heals to keep the core alive. You are protected by the core.",
        "8k+ HPS. You only need to heal players in the core (~5 blocks wide) "
        "and rarely leave its vicinity.",
        f"{DOCS_BASE}/#healer", f"{DOCS_BASE}/#healer-builds",
        threshold="8k+ HPS",
    ),
    Role.TANK: RoleGuidance(
        "Tank (Party Tank)",
        "Protect everyone from Anni's wrath; stand in the boss' face.",
        "100k+ EHP with 15k+ HPR. You absorb all direct damage and never "
        "retreat. Paladin (aggro draw, Heavenly Trumpet) is ideal.",
        f"{DOCS_BASE}/#tank", f"{DOCS_BASE}/#tank-builds",
        threshold="100k+ EHP with 15k+ HPR",
    ),
    Role.FILL: RoleGuidance(
        "Fill (Flexible Learner)",
        "Learn the mechanics with no requirements; rewards even if you die. "
        "Capacity is limited (≤30–40% of a party).",
        "None! Any build. Optionally help the tertiary DPS.",
        f"{DOCS_BASE}/#attending", f"{DOCS_BASE}/#attending",
        threshold="None!",
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

#: The capability modal's "unusual build" nudge (``capability.is_unusual_build``)
#: — advisory only, it never blocks a save. Healer and tank builds are expected
#: to run one of these weapons (lower-cased base names; a masterwork of one
#: counts too).
ROLE_WEAPONS: dict[Role, frozenset[str]] = {
    Role.HEALER: frozenset({"absolution", "lament", "monster", "halcyon"}),
    Role.TANK: frozenset({"guardian"}),
}
#: DPS roles instead expect a mythic and a class ABOVE this combat level (the
#: cap is 121). Primary wants both; secondary/tertiary are unusual only when
#: they have neither.
ROLE_CLASS_LEVELS: dict[Role, int] = {
    Role.PRIMARY: 120,
    Role.SECONDARY: 110,
    Role.TERTIARY: 100,
}

#: Epoch-0 sentinel: a WAPI ``lastJoin`` at/just-after the unix epoch means the
#: player has disabled their Wynncraft API (never appears online). Same
#: convention as dazebot's ``is_last_online_unknown`` (<= epoch + 1 day).
API_DISABLED_LAST_ONLINE_MAX = 86_400  # seconds past epoch
