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

from app.constants import DEFAULT_ENABLED_REGIONS, DEFAULT_STAFF_GUILD_RANKS


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
    staff_role_id: int = Field(
        default=1337993168502788216,
        description="Discord role ID that gates staff-only subcommands "
        "(e.g. `\\rsvp set` override). Members with this role on the invoking "
        "guild may set other users' RSVPs; everyone else gets a CheckFailure.",
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

    staff_guild_ranks: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_STAFF_GUILD_RANKS),
        description="WAPI guild member rank keys treated as staff — the "
        "lead-organiser candidate set (ALL of them, online or not). "
        "Comma-separated in env (STAFF_GUILD_RANKS); matched case-insensitively "
        "against the v3 guild ranks (owner/chief/strategist/captain/recruiter/"
        "recruit). Default = the management ranks; widen if recruiters organise.",
    )

    enabled_regions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [c.value for c in DEFAULT_ENABLED_REGIONS],
        description="MaxMind GeoIP2 continent codes offered in the "
        "preferred-region picker (ENABLED_REGIONS, comma-separated). Wynn "
        "currently only runs AS/EU/NA proxies, so those are the default — "
        "offering regions Wynn can't host just confuses users. Widen this "
        "(e.g. ENABLED_REGIONS=AS,EU,NA,OC) as Wynn adds proxies; codes "
        "outside the set stay valid and still display if already stored, "
        "they're simply not offered or saved. Empty => no picker.",
    )

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

    @field_validator("staff_guild_ranks", mode="before")
    @classmethod
    def _split_ranks(cls, v: object) -> object:
        """``STAFF_GUILD_RANKS`` as a comma string or list; trim + lower-case
        (WAPI v3 guild rank keys are lower-case). Unknown tokens are harmless
        (they just never match a real rank)."""
        items = v.split(",") if isinstance(v, str) else v
        if isinstance(items, (list, tuple)):
            return [s for t in items if (s := str(t).strip().lower())]
        return v

    @field_validator("enabled_regions", mode="before")
    @classmethod
    def _split_regions(cls, v: object) -> object:
        """Accept ``ENABLED_REGIONS`` as a comma string or a list; trim and
        UPPER-case each code (MaxMind continent codes are upper-case). Unknown
        tokens are tolerated here and dropped by ``app.domain.regions`` so a
        typo in env can't crash boot."""
        items = v.split(",") if isinstance(v, str) else v
        if isinstance(items, (list, tuple)):
            return [s for t in items if (s := str(t).strip().upper())]
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

    # --- Hot-window cadence ramp --------------------------------------------
    # From ``T - hot_window_open_seconds`` until ``stamp + grace`` the three
    # "who is online now" pollers switch from their normal cadence to the
    # ``*_hot_seconds`` variant. Goal: dashboard freshness ≥ vetsmod ``/wv list``
    # while the anni is forming. See ``app/services/hot_window.py``.
    #
    # Upstream limits (verified 2026-05-27, see app/services/wapi.py docstring):
    #
    # * ``/v1/outbound/list``: real-time push from vetsmod inbound, no TTL.
    # * WAPI ``/v3/guild/<name>``: ``Cache-Control: max-age=120`` (so WAPI data
    #   freshness is bounded at 120s regardless of how often we ask) and the
    #   GUILD ratelimit bucket allows 50 req / 60s. Our 5s hot cadence ⇒
    #   12 req/min, well inside. The point of polling faster than the WAPI
    #   TTL is the temp-server side (real-time), which is the source of truth
    #   for "is X online right now".
    hot_window_open_seconds: int = 120 * 60  # 2h before stamp_epoch
    online_merge_hot_seconds: int = 5
    presence_poll_hot_seconds: int = 2
    auto_promoter_seconds: int = 60          # idle cadence outside hot window
    auto_promoter_hot_seconds: int = 3       # ticks inside the hot window

    # Minimum interval between WAPI ``/v3/guild/<name>`` re-fetches inside
    # ``online_merge``. Matches the endpoint's ``Cache-Control: max-age=120``
    # so we don't burn calls (or upstream cloudflare bandwidth) on requests
    # that would return the same cached body. The cached payload is re-parsed
    # every tick so freshness between fetches still merges into the live
    # ``state.online_by_uuid``.
    wapi_guild_ttl_seconds: int = 120

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

    @property
    def staff_guild_rank_set(self) -> frozenset[str]:
        """Staff guild ranks as a lower-cased set (WAPI rank keys are
        lower-case) for O(1) membership checks in ``online_merge``."""
        return frozenset(r.lower() for r in self.staff_guild_ranks)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
