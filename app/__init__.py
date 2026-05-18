"""vets-anni — Wynnvets Annihilation coordination stack.

One process hosts three things on a single asyncio loop (lean for the 2 GB VPS):

* the **FastAPI** web app  — App1 (user dashboard) + App3 (staff organizer board)
* **fishbot** (discord.py) — App2 (`/rsvp`)
* background **pollers**   — anni timestamp, online-truth merge, staff, weapons, presence

Package layout (one responsibility per package):

* ``app.settings``   — environment-driven configuration
* ``app.constants``  — enums + domain data tables (roles, colours, attendance table)
* ``app.db``         — Tortoise models, config, lifecycle (Aerich migrations)
* ``app.domain``     — pure logic (no FastAPI/discord imports), unit-testable
* ``app.services``   — long-lived async pollers + outbound clients
* ``app.web``        — routers, auth, deps, the board WebSocket
* ``app.bot``        — fishbot

See ``.claude/CLAUDE.md`` for the documentation hub.
"""

__version__ = "0.1.0"
