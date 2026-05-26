"""Seed the LOCAL dev database with a realistic dummy dataset.

Purpose: let a developer run the site locally (VS Code -> Run & Debug, or
``python main.py``) and immediately see the dashboards/board rendered with
believable data — to confirm design choices and rendering without the live
vets API / Discord.

Real usernames are used so the UI looks authentic:

* MEMBER  — real Returners staff (from WAPI ``/v3/guild/Returners``).
* HONOURARY — Paradrex, Sevisoup, Minethuselah.
* ALLY    — real members of an ally-tagged guild (here ``Team CM``/TCM; any
  guild whose tag is in ``settings.ally_guild_tags`` qualifies).
* OTHER   — real members of guild ``Wynn``.
* COMMUNITY — foo / bar / baz (guildless placeholders).

UUIDs for the WAPI-sourced players and the honourary trio are their real
Minecraft UUIDs (so mc-heads.net renders an actual face rather than the Steve
default it serves for unknown UUIDs); only the community placeholders use a
deterministic synthetic UUID. The seed stays fully offline and idempotent.
It only touches the local SQLite file resolved from settings
(``ANNI_DB_PATH``) — NEVER point it at production.

    python scripts/seed_dev.py        # or the "vets-anni: seed dev data" launch
"""

from __future__ import annotations

import asyncio
import sys
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make ``app`` importable no matter how this script is launched.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from tortoise import Tortoise  # noqa: E402

from app.constants import (  # noqa: E402
    AttendanceNotice,
    BucketKind,
    ConfidenceLevel,
    MembershipTier,
    Role,
)
from app.db import lifecycle  # noqa: E402
from app.db.models import (  # noqa: E402
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    RoleCapabilityWeapon,
    Rsvp,
)

UTC = timezone.utc
EPOCH = datetime.fromtimestamp(0, tz=UTC)  # API-disabled sentinel


def _synth(name: str) -> str:
    """Deterministic synthetic UUID for names we have no real UUID for."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"vets-anni-dev:{name}"))


# (uuid, mc_username, tier, guild, wynn_username|None, last_online|None)
# Real UUIDs are from WAPI; honourary/community use a synthetic UUID.
M, H = MembershipTier.MEMBER, MembershipTier.HONOURARY
A, O, C = MembershipTier.ALLY, MembershipTier.OTHER, MembershipTier.COMMUNITY
PLAYERS: tuple[tuple, ...] = (
    # Returners staff (MEMBER)
    ("fa8aa700-4538-485f-bf91-325263606995", "Holidaze", M, "Returners", None, None),
    ("085d0e58-29d4-44aa-8379-92a4568a59d6", "Wenweia", M, "Returners", None, None),
    ("47dc57a6-a099-4de5-903b-26d4de617213", "Nazzae", M, "Returners", None, None),
    # API-disabled staff: never online, last_online == unix epoch sentinel.
    ("bb171c68-7080-4d02-be7b-cc2d5dcbcd82", "Metrafish", M, "Returners", None, EPOCH),
    # Rename desync: in-game (wynn) name differs from resolved mc name.
    ("94f0627c-bf74-4988-b741-5da7adbf99c2", "_akaPasta", M, "Returners",
     "ISnortPasta", None),
    ("c55a4c65-8486-4004-a70e-8c7b03ea86c1", "Faulischlumpf", M, "Returners",
     None, None),
    # Honourary — real Mojang UUIDs so mc-heads.net serves the actual skin
    # (synthetic UUIDs render as Steve since mc-heads returns the default
    # face with HTTP 200, bypassing the template's onerror fallback).
    ("081f3c72-2c43-4689-ad2c-91446a72734d", "Paradrex", H, None, None, None),
    ("62a4c5b5-7e50-4308-a313-1f3efb9c318d", "Sevisoup", H, None, None, None),
    ("60ea30a2-ddfb-4620-b465-832a20ba2205", "Minethuselah", H, None, None, None),
    # Ally — guild TCM (Team CM)
    ("a411f463-d32f-477f-b821-03fdea70a0d9", "Trixomaniac", A, "Team CM", None, None),
    ("4fbf7838-f795-4545-8bdb-f39c2a0a0835", "ThinKing", A, "Team CM", None, None),
    # Other — guild Wynn
    ("1ed075fc-5aa9-42e0-a29f-640326c1d80c", "Salted", O, "WYNN", None, None),
    ("b10436a1-bb7d-4894-b27a-983ec9f782dd", "Jumla", O, "WYNN", None, None),
    # Community (guildless)
    (_synth("foo"), "foo", C, None, None, None),
    (_synth("bar"), "bar", C, None, None, None),
    (_synth("baz"), "baz", C, None, None, None),
)

#: name -> AnniPlayer.preferred_regions CSV (MaxMind GeoIP2 continent codes).
#: A realistic spread incl. a multi-region player + several with no
#: preference (absent => "" => "Any region" in the UI).
PREFERRED_REGIONS: dict[str, str] = {
    "Wenweia": "EU,NA",      # plays both EU and NA worlds
    "Holidaze": "NA",
    "Nazzae": "EU",
    "_akaPasta": "EU",
    "Trixomaniac": "OC",
    "Salted": "AS",
    "Paradrex": "NA,SA",
}


async def _wipe() -> None:
    """Clear anni-domain rows (children first to respect FKs)."""
    for model in (
        RoleCapabilityWeapon, RoleCapability, BoardPlacement, Rsvp,
        Party, AnniEvent, AnniPlayer,
    ):
        await model.all().delete()


async def populate() -> dict[str, object]:
    """Build the dev dataset into the already-connected Tortoise DB.

    Assumes Tortoise is initialized and the schema exists; wipes the
    anni-domain rows first so re-running is idempotent. Returns handles
    (``{"players": {name: AnniPlayer}, "event": AnniEvent}``) so callers —
    notably the test-suite — can assert against the created rows without
    re-querying. Does NOT init or close connections; that is the caller's job
    (the script wraps it in :func:`main`, tests in the ``db`` fixture)."""
    await _wipe()

    now = datetime.now(UTC)
    p: dict[str, AnniPlayer] = {}
    for uid, name, tier, guild, wynn, last in PLAYERS:
        p[name] = await AnniPlayer.create(
            mc_uuid=uid, mc_username=name,
            wynn_username=wynn or name,
            guild=guild, membership_tier=tier,
            last_online=last if last is not None else now,
            preferred_regions=PREFERRED_REGIONS.get(name, ""),
        )

    # Concept-art shows "anni in 93 minutes" — match it so the countdown looks real.
    stamp = int((now + timedelta(minutes=93)).timestamp())
    event = await AnniEvent.create(
        stamp_epoch=stamp, is_active=True, organizer=p["Holidaze"]
    )

    # --- capabilities --------------------------------------------------------
    # Wenweia: multi-weapon PRIMARY (Labyrinth + Revolution) + a HEALER cap.
    cap = await RoleCapability.create(
        player=p["Wenweia"], role=Role.PRIMARY,
        confidence=ConfidenceLevel.HIGH, build_quality=ConfidenceLevel.HIGH,
        success_count=12,
    )
    # Subtypes are the real Wynncraft v3 `subType` values (verified against
    # POST /v3/item/search) so the seeded display matches the live catalog.
    await RoleCapabilityWeapon.create(capability=cap, weapon_name="Labyrinth",
                                      weapon_subtype="bow")
    await RoleCapabilityWeapon.create(capability=cap, weapon_name="Revolution",
                                      weapon_subtype="bow")
    cap = await RoleCapability.create(
        player=p["Wenweia"], role=Role.HEALER,
        confidence=ConfidenceLevel.MODERATE, build_quality=ConfidenceLevel.MODERATE,
        success_count=3,
    )
    await RoleCapabilityWeapon.create(capability=cap, weapon_name="Lament",
                                      weapon_subtype="wand")
    cap = await RoleCapability.create(
        player=p["Nazzae"], role=Role.HEALER,
        confidence=ConfidenceLevel.HIGH, build_quality=ConfidenceLevel.HIGH,
        success_count=8,
    )
    await RoleCapabilityWeapon.create(capability=cap, weapon_name="Absolution",
                                      weapon_subtype="relik")
    cap = await RoleCapability.create(
        player=p["_akaPasta"], role=Role.TANK,
        confidence=ConfidenceLevel.HIGH, build_quality=ConfidenceLevel.MODERATE,
        success_count=5,
    )
    await RoleCapabilityWeapon.create(capability=cap, weapon_name="Guardian",
                                      weapon_subtype="spear")
    cap = await RoleCapability.create(
        player=p["Paradrex"], role=Role.TERTIARY,
        confidence=ConfidenceLevel.MODERATE, build_quality=ConfidenceLevel.LOW,
        success_count=1,
    )
    await RoleCapabilityWeapon.create(capability=cap, weapon_name="Idol",
                                      weapon_subtype="spear")

    # --- parties -------------------------------------------------------------
    p1 = await Party.create(event=event, ordinal=1, host=p["Holidaze"],
                            world="AS5", stage=3)
    p2 = await Party.create(event=event, ordinal=2, host=p["Nazzae"], stage=1)

    # --- board placements (single instance per person) -----------------------
    async def place(name, *, party=None, bucket=None, role=None, late=False, i=0):
        await BoardPlacement.create(
            event=event, player=p[name], party=party, bucket=bucket,
            assigned_role=role, is_late=late, sort_index=i,
        )

    await place("Wenweia", party=p1, role=Role.PRIMARY, i=0)
    await place("Nazzae", party=p1, role=Role.HEALER, i=1)
    await place("_akaPasta", party=p1, role=Role.TANK, i=2)         # rename-desync
    await place("Minethuselah", party=p2, i=0)                       # no role -> gray
    await place("Faulischlumpf", bucket=BucketKind.UNASSIGNED, i=0)
    await place("Metrafish", bucket=BucketKind.UNASSIGNED, i=1)      # API-disabled
    await place("Paradrex", bucket=BucketKind.UNASSIGNED, i=2)
    await place("Trixomaniac", bucket=BucketKind.UNASSIGNED, i=3)
    await place("foo", bucket=BucketKind.UNASSIGNED, i=4)
    await place("baz", bucket=BucketKind.UNASSIGNED, i=5)             # Fill, no caps
    await place("Salted", bucket=BucketKind.UNASSIGNED, late=True, i=6)   # LATE
    await place("Jumla", bucket=BucketKind.UNASSIGNED, late=True, i=7)
    await place("Sevisoup", bucket=BucketKind.VOLUNTEERS, i=0)
    await place("bar", bucket=BucketKind.VOLUNTEERS, i=1)
    await place("ThinKing", bucket=BucketKind.WONTASSIGN, i=0)
    # Holidaze is the organiser — intentionally not placed on the board.

    # --- RSVPs ---------------------------------------------------------------
    for name, notice in (
        ("Wenweia", AttendanceNotice.RSVP_HARD),
        ("Nazzae", AttendanceNotice.RSVP_HARD),
        ("Metrafish", AttendanceNotice.RSVP_HARD),   # API-disabled but RSVP'd
        ("Trixomaniac", AttendanceNotice.RSVP_SOFT),
        ("Paradrex", AttendanceNotice.RSVP_SOFT),
        ("foo", AttendanceNotice.RSVP_HARD),
    ):
        await Rsvp.create(event=event, player=p[name], notice=notice)

    return {"players": p, "event": event}


async def _reset_schema() -> None:
    """Drop every table then recreate it from the *current* models.

    ``generate_schemas(safe=True)`` only CREATEs missing tables — it never
    ALTERs an existing one, so after any model change (e.g. a new column) a
    stale ``data/anni.db`` makes ``populate()`` fail on the first insert
    ("no such column"). We rebuild over the seeder's own connection rather
    than deleting the file: the file approach dies with a Windows
    ``PermissionError`` whenever the dev server / a paused debug session
    still holds ``data/anni.db`` open. This is a dev-only script (module
    docstring: *NEVER point it at production*) and debug DBs are disposable.
    """
    conn = Tortoise.get_connection("default")
    await conn.execute_script("PRAGMA foreign_keys=OFF;")
    rows = await conn.execute_query_dict(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%';"
    )
    for r in rows:
        await conn.execute_script(f'DROP TABLE IF EXISTS "{r["name"]}";')
    await conn.execute_script("PRAGMA foreign_keys=ON;")
    await Tortoise.generate_schemas(safe=True)


async def main() -> None:
    await lifecycle.init()
    # Rebuild the schema fresh from the models every run (see _reset_schema);
    # also covers a fresh clone where `aerich upgrade` hasn't run yet.
    await _reset_schema()
    await populate()
    await Tortoise.close_connections()
    print(
        f"Seeded dev data: 1 active event (anni ~93m, organiser Holidaze), "
        f"{len(PLAYERS)} real-name players (incl. API-disabled Metrafish + "
        f"rename-desync _akaPasta), 2 parties (stages 3 & 1), "
        f"15 placements, 6 RSVPs. Run the dev server -> http://127.0.0.1:8000/"
    )


if __name__ == "__main__":
    asyncio.run(main())
