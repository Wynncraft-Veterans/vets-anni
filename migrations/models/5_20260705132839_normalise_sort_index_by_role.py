"""Data migration: normalise BoardPlacement.sort_index across every event so
each container (bucket-lane or party) has contiguous 0..N-1 values ordered by
role priority.

Pre-fix, sort_index reflected arrival order (tail-count pattern in
domain/buckets), and drag-reorder collided existing sort_indexes so the visible
order was unstable. Post-fix (see app/domain/buckets._upsert), sort_index is
the ordering key, renumbered on every mutation. This migration seeds the new
invariant for existing data so buckets render in role order (Primary >
Secondary > Tertiary > Healer > Tank > Fill > unassigned) without staff having
to touch anything.

Idempotent: ORDER BY (role_priority, sort_index) is stable, so a second run
sees sort_indexes already in role-priority order and yields the same
numbering. Downgrade is a no-op - pre-migration ordering is not
reconstructable.
"""
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "board_placement" SET "sort_index" = subq.new_idx
        FROM (
            SELECT "id", ROW_NUMBER() OVER (
                PARTITION BY "event_id", "party_id", "bucket", "is_late", "is_walkin"
                ORDER BY
                    CASE "assigned_role"
                        WHEN 'primary' THEN 0
                        WHEN 'secondary' THEN 1
                        WHEN 'tertiary' THEN 2
                        WHEN 'healer' THEN 3
                        WHEN 'tank' THEN 4
                        WHEN 'fill' THEN 5
                        ELSE 6
                    END,
                    "sort_index"
            ) - 1 AS new_idx
            FROM "board_placement"
        ) AS subq
        WHERE "board_placement"."id" = subq."id";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return ""


MODELS_STATE = (
    "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"
)
