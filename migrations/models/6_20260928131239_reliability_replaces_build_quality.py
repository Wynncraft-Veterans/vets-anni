from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "setback" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "kind" VARCHAR(12) NOT NULL /* LOSS: loss\nMISSED: missed */,
    "role" VARCHAR(16) /* PRIMARY: primary\nSECONDARY: secondary\nTERTIARY: tertiary\nHEALER: healer\nTANK: tank\nFILL: fill */,
    "occurred_at" TIMESTAMP NOT NULL /* The anni's stamp — the calendar month this counts against. */,
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "event_id" CHAR(36) REFERENCES "anni_event" ("id") ON DELETE SET NULL,
    "player_id" VARCHAR(36) NOT NULL REFERENCES "anni_player" ("mc_uuid") ON DELETE CASCADE
) /* One bad outcome, recorded by the grace-wipe, that can cost the player */;
        ALTER TABLE "role_capability" DROP COLUMN "build_quality";
        ALTER TABLE "rsvp" ADD "seen_online_at" TIMESTAMP /* First time the presence poller saw them online inside the hot window */;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "role_capability" ADD "build_quality" VARCHAR(12) NOT NULL DEFAULT 'moderate' /* HIGH: high\nMODERATE: moderate\nLOW: low */;
        ALTER TABLE "rsvp" DROP COLUMN "seen_online_at";
        DROP TABLE IF EXISTS "setback";"""


MODELS_STATE = (
    "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"
)
