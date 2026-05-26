from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "anni_player" (
    "mc_uuid" VARCHAR(36) NOT NULL PRIMARY KEY,
    "mc_username" VARCHAR(32) NOT NULL,
    "wynn_username" VARCHAR(32),
    "guild" VARCHAR(64),
    "membership_tier" VARCHAR(16) NOT NULL DEFAULT 'other' /* MEMBER: member\nWAITLIST: waitlist\nHONOURARY: honourary\nCOMMUNITY: community\nALLY: ally\nOTHER: other */,
    "preferred_regions" VARCHAR(32) NOT NULL DEFAULT '',
    "last_online" TIMESTAMP,
    "last_seen_server" VARCHAR(16),
    "server_observed_at" TIMESTAMP,
    "password_hash" VARCHAR(128),
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) /* A person we know about — the subject of dashboards and the board. */;
CREATE TABLE IF NOT EXISTS "anni_event" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "stamp_epoch" BIGINT NOT NULL,
    "announced_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "grace_opened_at" TIMESTAMP,
    "wiped_at" TIMESTAMP,
    "is_active" INT NOT NULL DEFAULT 1,
    "organizer_id" VARCHAR(36) REFERENCES "anni_player" ("mc_uuid") ON DELETE SET NULL
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
    "host_id" VARCHAR(36) REFERENCES "anni_player" ("mc_uuid") ON DELETE SET NULL,
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
    "player_id" VARCHAR(36) NOT NULL REFERENCES "anni_player" ("mc_uuid") ON DELETE CASCADE,
    CONSTRAINT "uid_board_place_event_i_2c0adf" UNIQUE ("event_id", "player_id"),
    CONSTRAINT "ck_board_placement_xor" CHECK (("bucket" IS NULL) <> ("party_id" IS NULL))
) /* Where a person sits on the organizer board for an event. */;
CREATE TABLE IF NOT EXISTS "role_capability" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "role" VARCHAR(16) NOT NULL /* PRIMARY: primary\nSECONDARY: secondary\nTERTIARY: tertiary\nHEALER: healer\nTANK: tank\nFILL: fill */,
    "confidence" VARCHAR(12) NOT NULL /* HIGH: high\nMODERATE: moderate\nLOW: low */,
    "build_quality" VARCHAR(12) NOT NULL /* HIGH: high\nMODERATE: moderate\nLOW: low */,
    "success_count" INT NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "player_id" VARCHAR(36) NOT NULL REFERENCES "anni_player" ("mc_uuid") ON DELETE CASCADE,
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
    "player_id" VARCHAR(36) NOT NULL REFERENCES "anni_player" ("mc_uuid") ON DELETE CASCADE,
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
)
