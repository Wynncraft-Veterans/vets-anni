from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "anni_player" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "mc_uuid" VARCHAR(36) NOT NULL UNIQUE,
    "mc_username" VARCHAR(32) NOT NULL,
    "wynn_username" VARCHAR(32),
    "guild" VARCHAR(64),
    "membership_tier" VARCHAR(16) NOT NULL DEFAULT 'other' /* MEMBER: member\nWAITLIST: waitlist\nHONOURARY: honourary\nCOMMUNITY: community\nALLY: ally\nOTHER: other */,
    "last_online" TIMESTAMP,
    "last_seen_server" VARCHAR(16),
    "server_observed_at" TIMESTAMP,
    "password_hash" VARCHAR(128),
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) /* A person we know about — the subject of dashboards and the board. */;
CREATE INDEX IF NOT EXISTS "idx_anni_player_mc_uuid_4e1c2d" ON "anni_player" ("mc_uuid");
CREATE TABLE IF NOT EXISTS "anni_event" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "stamp_epoch" BIGINT NOT NULL,
    "announced_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "grace_opened_at" TIMESTAMP,
    "wiped_at" TIMESTAMP,
    "is_active" INT NOT NULL DEFAULT 1,
    "organizer_id" CHAR(36) REFERENCES "anni_player" ("id") ON DELETE SET NULL
) /* One announced annihilation. Exactly one row has ``is_active=True`` at a */;
CREATE INDEX IF NOT EXISTS "idx_anni_event_stamp_e_e547ae" ON "anni_event" ("stamp_epoch");
CREATE INDEX IF NOT EXISTS "idx_anni_event_is_acti_ac2c86" ON "anni_event" ("is_active");
CREATE TABLE IF NOT EXISTS "app_config" (
    "key" VARCHAR(64) NOT NULL PRIMARY KEY,
    "value_json" TEXT NOT NULL,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) /* Key\/value runtime config + admin-rotatable secrets (mirrors dazebot's */;
CREATE TABLE IF NOT EXISTS "mojang_name_cache" (
    "mc_uuid" VARCHAR(36) NOT NULL PRIMARY KEY,
    "username" VARCHAR(32) NOT NULL,
    "refreshed_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) /* uuid -> current username cache for offline rename-desync resolution */;
CREATE TABLE IF NOT EXISTS "party" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "ordinal" INT NOT NULL,
    "world" VARCHAR(16),
    "stage" INT NOT NULL DEFAULT 1,
    "result" VARCHAR(8) NOT NULL DEFAULT 'tbd' /* TBD: tbd\nLOSS: loss\nLAG: lag\nWIN: win */,
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "event_id" CHAR(36) NOT NULL REFERENCES "anni_event" ("id") ON DELETE CASCADE,
    "host_id" CHAR(36) REFERENCES "anni_player" ("id") ON DELETE SET NULL,
    CONSTRAINT "uid_party_event_i_eaa863" UNIQUE ("event_id", "ordinal")
) /* A 10-slot party for an event. ``stage`` (1..5) and ``result`` propagate */;
CREATE TABLE IF NOT EXISTS "board_placement" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "bucket" VARCHAR(24) /* UNASSIGNED: unassigned\nWONTASSIGN: wontassign\nVOLUNTEERS: volunteers */,
    "assigned_role" VARCHAR(16) /* PRIMARY: primary\nSECONDARY: secondary\nTERTIARY: tertiary\nHEALER: healer\nTANK: tank\nFILL: fill */,
    "is_late" INT NOT NULL DEFAULT 0,
    "sort_index" INT NOT NULL DEFAULT 0,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "event_id" CHAR(36) NOT NULL REFERENCES "anni_event" ("id") ON DELETE CASCADE,
    "party_id" CHAR(36) REFERENCES "party" ("id") ON DELETE SET NULL,
    "player_id" CHAR(36) NOT NULL REFERENCES "anni_player" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_board_place_event_i_2c0adf" UNIQUE ("event_id", "player_id")
) /* Where a person sits on the organizer board for an event. */;
CREATE TABLE IF NOT EXISTS "role_capability" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "role" VARCHAR(16) NOT NULL /* PRIMARY: primary\nSECONDARY: secondary\nTERTIARY: tertiary\nHEALER: healer\nTANK: tank\nFILL: fill */,
    "confidence" VARCHAR(12) NOT NULL /* HIGH: high\nMODERATE: moderate\nLOW: low */,
    "build_quality" VARCHAR(12) NOT NULL /* HIGH: high\nMODERATE: moderate\nLOW: low */,
    "success_count" INT NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "player_id" CHAR(36) NOT NULL REFERENCES "anni_player" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_role_capabi_player__dad919" UNIQUE ("player_id", "role")
) /* A user's self-declared ability in one core role. At most one row per */;
CREATE TABLE IF NOT EXISTS "role_capability_weapon" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "weapon_name" VARCHAR(64) NOT NULL,
    "weapon_subtype" VARCHAR(12) NOT NULL,
    "capability_id" CHAR(36) NOT NULL REFERENCES "role_capability" ("id") ON DELETE CASCADE
) /* A weapon the user owns\/uses for a capability (many per capability). */;
CREATE TABLE IF NOT EXISTS "rsvp" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "notice" VARCHAR(16) NOT NULL /* ATTEND_EARLY: attend_early\nRSVP_HARD: rsvp_hard\nRSVP_SOFT: rsvp_soft\nATTEND_LATE: attend_late */,
    "source" VARCHAR(16) NOT NULL DEFAULT 'discord',
    "revoked_at" TIMESTAMP,
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "event_id" CHAR(36) NOT NULL REFERENCES "anni_event" ("id") ON DELETE CASCADE,
    "player_id" CHAR(36) NOT NULL REFERENCES "anni_player" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_rsvp_event_i_f1c4da" UNIQUE ("event_id", "player_id")
) /* A user's RSVP for an event, set via fishbot ``\/rsvp``. Revoke is a soft */;
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSON NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
    "HVHME9fHx2+Kjyk2yOBsnmbznNvLP/8HcoqdMA=="
)
