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
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
        description="The anni channel (where fishbot will post public RSVP confirmation lines). "
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
    ally_guild_tags: Annotated[list[str], NoDecode] = Field(
        default=["SSNE", "TCM", "VSI", "BELL"],
        description="Wynncraft guild tags (prefixes) treated as Ally. "
        "Comma-separated in env (ALLY_GUILD_TAGS). Matched EXACTLY — Wynncraft "
        "guild tags are case-sensitive, so spell them as they appear in-game. "
        "Tags (and names) are NOT unique on Wynncraft; this is safe only "
        "because tiering is RSVP-gated and dupes are inactive — see "
        ".claude/domain_rules.md (Membership). Empty => no guild is Ally.",
    )
    returners_guild_name: str = Field(default="Returners")

    @field_validator("ally_guild_tags", mode="before")
    @classmethod
    def _split_tags(cls, v: object) -> object:
        """Accept a comma-separated env string or a list; trim each tag.

        Only whitespace and empty entries are stripped — case is preserved
        because Wynncraft guild tags are case-sensitive (``SSNE`` != ``ssne``).
        ``NoDecode`` keeps pydantic-settings from JSON-decoding the env value,
        so a plain ``SSNE,TCM,VSI,BELL`` reaches this validator as a string.
        """
        items = v.split(",") if isinstance(v, str) else v
        if isinstance(items, (list, tuple)):
            return [s for t in items if (s := str(t).strip())]
        return v

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

    @property
    def ally_guild_tag_set(self) -> frozenset[str]:
        """Ally tags as a set for O(1) membership checks.

        A guild is ALLY iff its tag is in this set, compared **exactly** —
        Wynncraft guild tags are case-sensitive. See ``.claude/domain_rules.md``
        (Membership).
        """
        return frozenset(self.ally_guild_tags)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
