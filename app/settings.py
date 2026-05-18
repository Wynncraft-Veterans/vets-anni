"""Environment-driven configuration.

All tunables live here so nothing reaches for ``os.environ`` directly. Values
come from the process environment (and a local ``.env`` in dev via
``python-dotenv``). On the VPS the values are injected by
``vets-deploy/stacks/vets-anni/.env``.

Cadences are seconds and deliberately conservative — the VPS is small and our
WAPI token has its own (separate, mandated) ratelimit bucket. They can be
overridden at runtime via the ``AppConfig`` table without a redeploy (see
``app.db.lifecycle``); the values here are the boot defaults.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Instantiate via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Discord (fishbot) ---------------------------------------------------
    fishbot_token: str = Field(default="", description="Discord bot token for fishbot.")
    rsvp_channel_id: int | None = Field(
        default=None,
        description="Channel where fishbot posts public RSVP confirmation lines. "
        "User replies stay ephemeral; this line is a visibility/record ack.",
    )

    # --- Wynncraft API (OWN token; separate ratelimit bucket) ----------------
    wapi_token: str = Field(default="", description="vets-anni's OWN Wynncraft token.")
    wapi_base: str = Field(default="https://api.wynncraft.com/v3")

    # --- Web auth (intentionally low-trust coordination tool) ----------------
    session_secret: str = Field(
        default="dev-insecure-change-me",
        description="itsdangerous cookie signing key.",
    )
    staff_password: str = Field(
        default="", description="Bootstrap staff password (hashed into AppConfig)."
    )
    admin_password: str = Field(
        default="", description="Gate for the staff-password rotate route."
    )

    # --- Domain config -------------------------------------------------------
    ally_guild_id: str = Field(
        default="",
        description="Wynncraft guild (id or name) treated as Ally. Empty => none.",
    )
    returners_guild_name: str = Field(default="Returners")

    # --- Sibling service integration ----------------------------------------
    vets_api_base: str = Field(default="https://api.wynnvets.org")
    dazebot_introspect_secret: str = Field(default="")
    dazebot_anni_identity_url: str = Field(
        default="http://dazebot:9421/api/internal/anni-identity"
    )

    # --- Misc ----------------------------------------------------------------
    public_base_url: str = Field(default="https://anni.wynnvets.org")
    anni_db_path: str = Field(
        default="./data/anni.db",
        description="SQLite file path. Docker overrides to /app/data/anni.db.",
    )
    debug: bool = Field(default=False)

    # --- Poller cadences (seconds) ------------------------------------------
    stamp_poll_seconds: int = 30
    staff_poll_seconds: int = 20
    online_merge_seconds: int = 25
    presence_poll_seconds: int = 10
    weapons_poll_seconds: int = 3600
    api_disabled_probe_seconds: int = 300
    lifecycle_poll_seconds: int = 30

    # Grace window (hours) for staff to record per-party results before wipe.
    grace_hours: int = 2

    @property
    def db_url(self) -> str:
        """Tortoise connection string for the SQLite file."""
        return f"sqlite://{self.anni_db_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
