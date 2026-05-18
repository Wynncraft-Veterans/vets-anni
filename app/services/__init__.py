"""Outbound clients + background pollers.

Nothing here imports FastAPI or discord. Pollers are plain asyncio loops
started from ``main.lifespan`` (the temporary-server pattern); they read
upstream services and write last-good snapshots into the shared
:class:`app.services.state.AppState` that lives on ``app.state``.

A bad tick never kills a loop — every poll iteration is wrapped in a
try/except that logs and continues, so a flaky upstream degrades to "serve
the last good snapshot" instead of taking the process down.
"""
