"""Pure domain logic — no FastAPI, Tortoise, or discord imports.

Everything here is a function of its arguments (the few async helpers only do
*injected* I/O, e.g. a Mojang lookup callable) so the rules are unit-testable
without a DB, network, or app. Vocabulary/data tables live in
``app.constants``; this package is the *behaviour* over them.
"""
