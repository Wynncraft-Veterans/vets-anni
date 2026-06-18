"""The single shared runtime cache.

One :class:`AppState` instance lives on ``app.state.appstate`` (created in
``main.create_app`` so it exists even under the test ASGI transport, which
skips ``lifespan``). Pollers write last-good snapshots here; web routes read
them per request. Not thread-safe by design — the whole process is one
asyncio loop (mirrors temporary-server ``app/services/state.py``).

Every field is a *last-good* cache: if an upstream poll fails, the previous
snapshot stays in place and `*_fetched_at` simply stops advancing, so the UI
keeps working (just slightly stale) instead of erroring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Maximum age (seconds) of a ``party_leader_by_uuid`` entry before
#: ``presence_poller`` treats it as stale and falls back to ``ONLINE_WORLD``.
#: vetsmod pushes ``anni_party_observation`` on every party event during the
#: anni window (snapshot updates also fire it), so a 60s window safely covers
#: the typical between-event idle without stranding stale corroboration when a
#: vetsmod client disconnects mid-window.
_PARTY_LEADER_TTL_SECONDS = 60


@dataclass(frozen=True)
class OnlinePlayer:
    """One entry of the online-merge result (mirrors vetsmod's merged view).

    ``queued`` players are *connecting*, not offline — anni is queue-heavy, so
    the presence machine treats them as ``ONLINE_ELSEWHERE`` (see
    ``app/domain/presence.py``). ``server`` is best-effort (``None`` unless a
    source reported it, e.g. the staff endpoint).
    """

    uuid: str
    username: str
    tier: str = "guild"          # guild | waitlist | honourary (from /v1/outbound/list)
    queued: bool = False
    server: str | None = None


@dataclass
class AppState:
    """Mutable process cache. Grouped by the poller that owns each block."""

    # --- stamp_poller --------------------------------------------------------
    #: Latest announced anni unix epoch. ``None`` => nothing announced (the
    #: ``/v1/outbound/stamp`` source was empty or unparseable). A *past* value
    #: is kept verbatim — the grace/wipe transition is Phase 2's lifecycle
    #: task; Phase 1 only ever *creates/updates* the active event for a future
    #: stamp and never deletes.
    stamp_epoch: int | None = None
    stamp_fetched_at: float = 0.0

    # --- staff_poller --------------------------------------------------------
    #: uuid -> {"uuid","username","rank","online","server"} (online staff only).
    online_staff: dict[str, dict] = field(default_factory=dict)
    staff_fetched_at: float = 0.0

    # --- online_merge: full guild staff (organiser candidates) --------------
    #: uuid -> {"uuid","username","rank"} for EVERY guild member whose rank is
    #: in ``settings.staff_guild_rank_set`` — online OR offline. The
    #: lead-organiser candidate set; ``online_merge`` fills it from the WAPI
    #: guild payload it already fetches (kept last-good on a failed tick).
    guild_staff: dict[str, dict] = field(default_factory=dict)
    guild_staff_fetched_at: float = 0.0

    # --- online_merge --------------------------------------------------------
    #: uuid -> OnlinePlayer. THE online-truth set (mirror of vetsmod /wv list).
    online_by_uuid: dict[str, OnlinePlayer] = field(default_factory=dict)
    online_fetched_at: float = 0.0
    #: uuid -> current username (authoritative roster names).
    roster_by_uuid: dict[str, str] = field(default_factory=dict)
    #: lowercased legacy/old name -> uuid (rename-desync resolution).
    aliases: dict[str, str] = field(default_factory=dict)
    roster_fetched_at: float = 0.0

    # --- weapons_poller ------------------------------------------------------
    #: lowercased weapon name -> subtype (bow/spear/wand/dagger/relik). The
    #: validated catalog the add-capability UI autocompletes against.
    weapons_by_name: dict[str, str] = field(default_factory=dict)
    weapons_fetched_at: float = 0.0

    # --- presence_poller (Phase 2) ------------------------------------------
    #: mc_uuid -> PresenceStatus.value, for the active event's board members
    #: only. The presence poller owns it (diffs it tick-over-tick and pushes a
    #: PATCH to the board hub); the staff board + the user dashboard read it so
    #: a non-WS client (or an SSR first paint) still shows the same status.
    presence_by_uuid: dict[str, str] = field(default_factory=dict)
    presence_fetched_at: float = 0.0

    # --- anni_party_observation (S7 vetsmod back-report) -------------------
    #: member_mc_uuid -> leader_mc_uuid. Source: S7 ``anni_party_observation``
    #: endpoint — vetsmod reports its local Wynncraft party roster when an
    #: organiser username appears in the party (the only signal that matters
    #: for the ``ONLINE_PARTY`` board upgrade). Names arrive over the wire and
    #: are resolved here against ``roster_by_uuid`` + ``aliases``. Entries
    #: older than :data:`_PARTY_LEADER_TTL_SECONDS` are treated as stale by
    #: ``presence_poller`` (a vetsmod disconnect mid-window must not pin the
    #: user to ``ONLINE_PARTY`` forever). Empty / stale / unresolved name =>
    #: classifier falls back to ``ONLINE_WORLD`` rather than fabricating
    #: ``ONLINE_PARTY``.
    party_leader_by_uuid: dict[str, str] = field(default_factory=dict)
    party_observation_fetched_at: float = 0.0

    # --- api_disabled probe (Phase 2) ---------------------------------------
    #: mc_uuids of API-disabled players the slow probe currently *infers* are
    #: active (a between-tick lastSeen/server change — dazebot purgelist
    #: style). Read by the presence poller as an extra "online" signal so an
    #: inferred-active hidden player becomes ONLINE_ELSEWHERE instead of
    #: UNKNOWN. We never put a player here unless we actually saw movement —
    #: the spec's "never fabricate online" rule.
    api_active_uuids: set[str] = field(default_factory=set)
    api_probe_at: float = 0.0

    # --- helpers -------------------------------------------------------------
    def is_online(self, uuid: str) -> OnlinePlayer | None:
        """Return the online-merge entry for ``uuid`` (``None`` if offline)."""
        return self.online_by_uuid.get(uuid)

    def resolve_uuid(self, ign: str) -> str | None:
        """IGN -> uuid using the cached roster then the legacy-name aliases.

        Case-insensitive. This is the *cheap* (no-network) half of identity
        resolution; ``app.domain.identity`` falls back to Mojang when this
        misses. Returns ``None`` when neither cache knows the name.
        """
        if not ign:
            return None
        needle = ign.strip().lower()
        for uuid, name in self.roster_by_uuid.items():
            if name.lower() == needle:
                return uuid
        return self.aliases.get(needle)

    def weapon_subtype(self, weapon_name: str) -> str | None:
        """Return the subtype iff ``weapon_name`` is in the validated catalog.

        Empty catalog (weapons poll has not succeeded yet) => ``None`` for
        everything; callers treat that as "cannot validate right now".
        """
        return self.weapons_by_name.get(weapon_name.strip().lower())

    def touch(self, attr: str) -> None:
        """Stamp ``<attr>`` with the current monotonic-ish wall clock."""
        setattr(self, attr, time.time())
