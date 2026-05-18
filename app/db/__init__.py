"""Persistence layer: Tortoise-ORM models, connection config, and lifecycle.

vets-anni stores only *anni-domain* data plus a thin UUID-keyed profile cache.
dazebot remains the owner of the Discord<->Minecraft link and tier resolution;
we never duplicate that. The Minecraft UUID is the join key everywhere.

Migrations use **Aerich** (``migrations/`` is committed). This is a deliberate
divergence from dazebot, which uses ``generate_schemas`` and has no migrations
— the spec explicitly requires migratable schema.
"""
