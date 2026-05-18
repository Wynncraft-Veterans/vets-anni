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
    "result" VARCHAR(8) NOT NULL DEFAULT 'pending' /* PENDING: pending\nLOSS: loss\nLAG: lag\nWIN: win */,
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "event_id" CHAR(36) NOT NULL REFERENCES "anni_event" ("id") ON DELETE CASCADE,
    "host_id" CHAR(36) REFERENCES "anni_player" ("id") ON DELETE SET NULL,
    CONSTRAINT "uid_party_event_i_eaa863" UNIQUE ("event_id", "ordinal")
) /* A 10-slot party for an event. ``stage`` (1..5) and ``result`` propagate */;
CREATE TABLE IF NOT EXISTS "board_placement" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "bucket" VARCHAR(24) /* UNASSIGNED: unassigned\nCONFIRMED_NONATTENDANCE: confirmed_nonattendance\nWILLING_TO_SIT_OUT: willing_to_sit_out */,
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
    "notice" VARCHAR(16) NOT NULL /* ONE_HR_EARLY: one_hr_early\nHARD_RSVP: hard_rsvp\nSOFT_RSVP: soft_rsvp\nLATE: late\nNONE: none */,
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
)
