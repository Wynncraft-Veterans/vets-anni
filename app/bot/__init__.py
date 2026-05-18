"""fishbot — the vets-anni Discord bot (App2).

Runs in the SAME process as the FastAPI app (a lifespan task on the shared
event loop), mirroring how dazebot hosts its API and how temporary-server
hosts its bridge bot. Cogs autoload from ``app.bot.cogs`` (port of dazebot's
``bot.py:_load_cogs``). Identity (Discord -> Minecraft) is resolved by calling
dazebot's secret-gated internal endpoint; fishbot never duplicates linking.
"""
