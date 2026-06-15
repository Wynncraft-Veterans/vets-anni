"""Tortoise-ORM models — the full anni-domain schema.

Design notes (see ``.claude/data_model.md`` for the narrative version):

* **Identity anchor is ``AnniPlayer.mc_uuid``.** Every other table joins on it.
* **``BoardPlacement`` enforces the single-instance-per-person invariant** via
  ``unique_together(("event", "player"))`` — a person can be in exactly one
  bucket *or* one party per event, never two places, never duplicated.
* Enums are stored as strings (``CharEnumField``) so rows stay human-readable
  and adding an enum value is a data change, not a migration of existing rows.
* ``last_online`` follows dazebot's epoch-sentinel convention: a value at/just
  after the unix epoch means the player disabled their Wynncraft API.
"""

from __future__ import annotations

from tortoise import fields
from tortoise.models import Model

from app.constants import (
    AttendanceNotice,
    BucketKind,
    ConfidenceLevel,
    MembershipTier,
    PartyResult,
    Role,
)


class AnniPlayer(Model):
    """A person we know about — the subject of dashboards and the board.

    Populated lazily: web login (IGN), ``/rsvp`` (via dazebot lookup), or the
    online-merge poller. ``wynn_username`` is the (possibly stale) in-game name
    used to detect/display rename desync; ``mc_username`` is the resolved one.
    """

    mc_uuid = fields.CharField(max_length=36, primary_key=True)
    mc_username = fields.CharField(max_length=32)
    wynn_username = fields.CharField(max_length=32, null=True)

    guild = fields.CharField(max_length=64, null=True)  # last observed guild
    membership_tier = fields.CharEnumField(
        MembershipTier, max_length=16, default=MembershipTier.OTHER
    )

    # User-set preferred play region(s): a CSV of MaxMind GeoIP2 continent
    # codes (``app.constants.ContinentCode``; "" = no preference). Stored as a
    # readable CSV — not a child table — because it is a tiny fixed-vocabulary
    # preference; parsed/formatted via ``app.domain.regions``. Shown on the
    # user's General module and (Phase 2) the staff board person card.
    preferred_regions = fields.CharField(max_length=32, default="")

    # Epoch-sentinel == API disabled (see app.constants.API_DISABLED_*).
    last_online = fields.DatetimeField(null=True)
    last_seen_server = fields.CharField(max_length=16, null=True)   # e.g. "EU37"
    server_observed_at = fields.DatetimeField(null=True)

    # First password set "sticks"; null => zero-friction login. Staff-resettable.
    password_hash = fields.CharField(max_length=128, null=True)

    # True iff this row was materialised by the auto-promoter for an online
    # player we'd never seen before (so the board can show a stub card with
    # blank stats + an "Unregistered" pill). Cleared on first meaningful
    # interaction (dashboard login, RSVP, capability edit, staff walk-in) by
    # ``app.domain.identity.mark_registered``; once cleared, never re-set.
    is_placeholder = fields.BooleanField(default=False)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    capabilities: fields.ReverseRelation[RoleCapability]

    class Meta:
        table = "anni_player"

    def __str__(self) -> str:  # pragma: no cover - debug aid
        return f"AnniPlayer({self.mc_username}/{self.mc_uuid})"


class RoleCapability(Model):
    """A user's self-declared ability in one core role. At most one row per
    (player, role). ``success_count`` increments when a party they were
    assigned this role in is recorded as a WIN (grace-wipe time)."""

    id = fields.UUIDField(primary_key=True)
    player = fields.ForeignKeyField(
        "models.AnniPlayer", related_name="capabilities", on_delete=fields.CASCADE
    )
    role = fields.CharEnumField(Role, max_length=16)
    confidence = fields.CharEnumField(ConfidenceLevel, max_length=12)
    build_quality = fields.CharEnumField(ConfidenceLevel, max_length=12)
    success_count = fields.IntField(default=0)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    weapons: fields.ReverseRelation[RoleCapabilityWeapon]

    class Meta:
        table = "role_capability"
        unique_together = (("player", "role"),)


class RoleCapabilityWeapon(Model):
    """A weapon the user owns/uses for a capability (many per capability).
    Validated against the cached WAPI weapons catalog at write time."""

    id = fields.UUIDField(primary_key=True)
    capability = fields.ForeignKeyField(
        "models.RoleCapability", related_name="weapons", on_delete=fields.CASCADE
    )
    weapon_name = fields.CharField(max_length=64)
    weapon_subtype = fields.CharField(max_length=12)  # bow/spear/wand/dagger/relik

    class Meta:
        table = "role_capability_weapon"


class AnniEvent(Model):
    """One announced annihilation. Exactly one row has ``is_active=True`` at a
    time (enforced in ``app.db.lifecycle``). The board/RSVPs hang off this; the
    2 h grace + wipe lifecycle is driven by ``stamp_epoch``."""

    id = fields.UUIDField(primary_key=True)
    stamp_epoch = fields.BigIntField(index=True)  # the announced unix timestamp
    announced_at = fields.DatetimeField(auto_now_add=True)
    grace_opened_at = fields.DatetimeField(null=True)
    wiped_at = fields.DatetimeField(null=True)

    organizer = fields.ForeignKeyField(
        "models.AnniPlayer", related_name="organizing",
        null=True, on_delete=fields.SET_NULL,
    )
    is_active = fields.BooleanField(default=True, index=True)
    # Colourblind mode is purely a per-user cookie (only a few users need it);
    # there is intentionally no event/global default to manage.

    parties: fields.ReverseRelation[Party]
    placements: fields.ReverseRelation[BoardPlacement]
    pings: fields.ReverseRelation[AnniEventPing]

    class Meta:
        table = "anni_event"


class AnniEventPing(Model):
    """Records each role-ping fishbot has sent for an event. The
    unique_together constraint caps total pings per event at one of
    each kind (first_notice + t_minus_90 = max 2 pings/occurrence).
    Insert happens BEFORE the Discord send so a crash between insert
    and send leaves us with a silent miss — preferable to a duplicate
    ping, and lets the cog/poller race resolve cleanly via the
    IntegrityError path."""

    id = fields.UUIDField(primary_key=True)
    event = fields.ForeignKeyField(
        "models.AnniEvent", related_name="pings", on_delete=fields.CASCADE,
    )
    kind = fields.CharField(max_length=16)  # "first_notice" | "t_minus_90"
    sent_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "anni_event_ping"
        unique_together = (("event", "kind"),)


class Party(Model):
    """A 10-slot party for an event. ``stage`` (1..5) and ``result`` propagate
    to user dashboards."""

    id = fields.UUIDField(primary_key=True)
    event = fields.ForeignKeyField(
        "models.AnniEvent", related_name="parties", on_delete=fields.CASCADE
    )
    ordinal = fields.IntField()  # display "Party N"
    host = fields.ForeignKeyField(
        "models.AnniPlayer", related_name="hosting",
        null=True, on_delete=fields.SET_NULL,
    )
    world = fields.CharField(max_length=16, null=True)
    stage = fields.IntField(default=1)
    result = fields.CharEnumField(PartyResult, max_length=8, default=PartyResult.TBD)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    members: fields.ReverseRelation[BoardPlacement]

    class Meta:
        table = "party"
        unique_together = (("event", "ordinal"),)


class BoardPlacement(Model):
    """Where a person sits on the organizer board for an event.

    THE single-instance-per-person table. Exactly one of (``bucket``,
    ``party``) is non-null; the unique (event, player) constraint guarantees a
    person can never be duplicated across buckets/parties. Every move is an
    UPSERT of this one row inside a transaction (see ``app.domain.buckets``).
    """

    id = fields.UUIDField(primary_key=True)
    event = fields.ForeignKeyField(
        "models.AnniEvent", related_name="placements", on_delete=fields.CASCADE
    )
    player = fields.ForeignKeyField(
        "models.AnniPlayer", related_name="placements", on_delete=fields.CASCADE
    )

    bucket = fields.CharEnumField(BucketKind, max_length=24, null=True)
    party = fields.ForeignKeyField(
        "models.Party", related_name="members",
        null=True, on_delete=fields.SET_NULL,
    )
    assigned_role = fields.CharEnumField(Role, max_length=16, null=True)
    # UNASSIGNED has three lanes: main (RSVP'd), walk-in (auto-detected
    # non-RSVP before T-60), late (anything placed after T-60). ``is_late``
    # wins if both are set; outside UNASSIGNED both are ignored.
    is_late = fields.BooleanField(default=False)
    is_walkin = fields.BooleanField(default=False)
    sort_index = fields.IntField(default=0)       # ordering within container

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "board_placement"
        unique_together = (("event", "player"),)


class Rsvp(Model):
    """A user's RSVP for an event, set via fishbot ``/rsvp``. Revoke is a soft
    delete (``revoked_at``) so we keep an audit trail."""

    id = fields.UUIDField(primary_key=True)
    event = fields.ForeignKeyField(
        "models.AnniEvent", related_name="rsvps", on_delete=fields.CASCADE
    )
    player = fields.ForeignKeyField(
        "models.AnniPlayer", related_name="rsvps", on_delete=fields.CASCADE
    )
    notice = fields.CharEnumField(AttendanceNotice, max_length=16)
    source = fields.CharField(max_length=16, default="discord")
    revoked_at = fields.DatetimeField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "rsvp"
        unique_together = (("event", "player"),)


class AppConfig(Model):
    """Key/value runtime config + admin-rotatable secrets (mirrors dazebot's
    ``BotConfigOverride``). Holds the hashed staff password, ally-guild
    override, escalation-timing overrides, etc. **No colourblind key** — CB is
    purely a per-user ``cb`` cookie with no global/event/admin default (the
    world default is always full colour); see ``app/web/deps.py`` and
    ``.claude/colourblind.md``. Sessions are signed cookies, so no session
    table is needed."""

    key = fields.CharField(max_length=64, primary_key=True)
    value_json = fields.TextField()
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "app_config"


class MojangNameCache(Model):
    """uuid -> current username cache for offline rename-desync resolution
    (same shape as dazebot's). Refreshed past ``MojangNameCache`` max-age."""

    mc_uuid = fields.CharField(max_length=36, primary_key=True)
    username = fields.CharField(max_length=32)
    refreshed_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mojang_name_cache"
