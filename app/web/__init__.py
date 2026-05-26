"""Web layer: FastAPI routers, auth/session, deps, and the board WebSocket.

App1 (user-facing) lives in ``routers/public.py`` + ``routers/user.py`` +
``routers/capability.py``. App3 (staff) lives in ``routers/staff.py`` +
``routers/organizer.py`` + ``routers/roles_dash.py`` +
``routers/staff_capability.py`` (the staff-side edit/delete twin of the
user's capability CRUD). ``deps.py`` centralises the Jinja environment,
signed-cookie sessions, and the colourblind toggle so every page renders
consistently.
"""
