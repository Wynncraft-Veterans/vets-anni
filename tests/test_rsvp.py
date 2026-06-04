"""``/rsvp`` — pin the spec'd subcommand behaviour at every layer.

The slash command is the single user-visible point of fishbot, so the
test surface is wide on purpose:

* The pure :mod:`app.domain.rsvp` writer (single-instance, soft-delete,
  revive-on-rsvp, ATTEND_EARLY/ATTEND_LATE refused).
* The :func:`execute_rsvp` decision tree (every branch: dazebot down,
  not linked, blocked, no active event, happy paths, plus a brand-new
  fishbot user → AnniPlayer get-or-create with tier from dazebot).
* The discord.py shim ``RsvpCog._handle`` (defers + ephemeral reply +
  conditional public post) driven by a ``_FakeContext``/``_FakeBot``,
  mirroring how the WS layer is tested against the hub directly
  (CLAUDE.md Phase-2 decisions). The cog is hybrid, so the same shim
  serves both ``/rsvp …`` (slash) and ``\\rsvp …`` (prefix) entrypoints.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field

import pytest

from app.constants import AttendanceNotice, MembershipTier
from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer, BoardPlacement, Rsvp
from app.domain import rsvp as rsvp_domain
from app.services.dazebot_client import AnniIdentity


# --------------------------------------------------------------------------- #
# Pure-domain layer                                                           #
# --------------------------------------------------------------------------- #


async def test_set_rsvp_creates_a_fresh_row(seeded):
    p = seeded["players"]["baz"]  # baz has no seeded RSVP
    event = seeded["event"]

    row = await rsvp_domain.set_rsvp(p, event, AttendanceNotice.RSVP_HARD)

    assert row.notice is AttendanceNotice.RSVP_HARD
    assert row.revoked_at is None
    assert row.source == "discord"
    assert await Rsvp.filter(event=event, player=p).count() == 1


async def test_set_rsvp_is_an_upsert_not_a_duplicate(seeded):
    """Single-instance per (event, player): repeat /rsvp calls update one row."""
    p = seeded["players"]["Wenweia"]  # seeded with HARD
    event = seeded["event"]

    row = await rsvp_domain.set_rsvp(p, event, AttendanceNotice.RSVP_SOFT)

    assert row.notice is AttendanceNotice.RSVP_SOFT
    assert await Rsvp.filter(event=event, player=p).count() == 1


async def test_set_rsvp_revives_a_revoked_row(seeded):
    p = seeded["players"]["Wenweia"]
    event = seeded["event"]
    revoked = await rsvp_domain.revoke(p, event)
    assert revoked is not None
    assert revoked.revoked_at is not None

    revived = await rsvp_domain.set_rsvp(p, event, AttendanceNotice.RSVP_SOFT)

    assert revived.id == revoked.id  # same row, not a new one
    assert revived.revoked_at is None
    assert revived.notice is AttendanceNotice.RSVP_SOFT
    assert await Rsvp.filter(event=event, player=p).count() == 1


@pytest.mark.parametrize("bad", [AttendanceNotice.ATTEND_EARLY, AttendanceNotice.ATTEND_LATE])
async def test_set_rsvp_refuses_derived_notices(seeded, bad):
    p = seeded["players"]["baz"]
    event = seeded["event"]
    with pytest.raises(ValueError):
        await rsvp_domain.set_rsvp(p, event, bad)


async def test_revoke_is_soft_delete(seeded):
    p = seeded["players"]["Wenweia"]
    event = seeded["event"]

    row = await rsvp_domain.revoke(p, event)

    assert row is not None and row.revoked_at is not None
    # The DB still has the row (audit), get_current ignores it.
    assert await Rsvp.filter(event=event, player=p).count() == 1
    assert await rsvp_domain.get_current(p, event) is None


async def test_revoke_no_active_is_a_noop(seeded):
    p = seeded["players"]["baz"]  # never RSVP'd
    event = seeded["event"]
    assert await rsvp_domain.revoke(p, event) is None
    assert await Rsvp.filter(event=event, player=p).count() == 0


async def test_get_current_ignores_revoked(seeded):
    p = seeded["players"]["Wenweia"]
    event = seeded["event"]
    assert (await rsvp_domain.get_current(p, event)) is not None
    await rsvp_domain.revoke(p, event)
    assert (await rsvp_domain.get_current(p, event)) is None


# --------------------------------------------------------------------------- #
# execute_rsvp decision tree                                                  #
# --------------------------------------------------------------------------- #


def _patch_identity(monkeypatch, identity: AnniIdentity | None):
    """Replace the dazebot client lookup with a fixed result.

    Patches the *name as the cog imported it* (``app.bot.cogs.rsvp``) so the
    cog's call site sees the fake without us touching the module-level
    singleton.
    """
    class _Fake:
        async def resolve_anni_identity(self, _discord_id):
            return identity
    from app.bot.cogs import rsvp as cog
    monkeypatch.setattr(cog, "get_dazebot_client", lambda: _Fake())


def _ident(player: AnniPlayer, *, tier: str = "member", **overrides) -> AnniIdentity:
    base = {
        "linked": True,
        "disc_uuid": "987654321",
        "mc_uuid": player.mc_uuid,
        "mc_username": player.mc_username,
        "tier": tier,
        "blocked": False,
        "reason": None,
    }
    base.update(overrides)
    return AnniIdentity(**base)


async def test_execute_returns_unavailable_when_dazebot_down(seeded, monkeypatch):
    _patch_identity(monkeypatch, None)
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "hard")

    assert "identity service is unavailable" in outcome.private_message.lower()
    assert outcome.public_message is None
    # Crucially: no DB write on a degraded path.
    assert await Rsvp.filter(source="discord").count() == 6  # seed unchanged


async def test_execute_refuses_blocked_link(seeded, monkeypatch):
    wen = seeded["players"]["Wenweia"]
    _patch_identity(
        monkeypatch,
        _ident(wen, blocked=True, reason="blacklisted for griefing"),
    )
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "hard")

    assert "blocked" in outcome.private_message.lower()
    assert "blacklisted for griefing" in outcome.private_message
    assert outcome.public_message is None


async def test_execute_refuses_unlinked_discord(seeded, monkeypatch):
    _patch_identity(
        monkeypatch,
        AnniIdentity(
            linked=False, disc_uuid="123",
            mc_uuid=None, mc_username=None, tier=None,
            blocked=False, reason="no linked minecraft account",
        ),
    )
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "hard")

    assert "verify" in outcome.private_message.lower()
    assert outcome.public_message is None


async def test_execute_no_active_event(db, monkeypatch):
    # No seeded fixture → no AnniEvent exists. Still need a player row for
    # the identity to point at; but we never get that far because the event
    # check fails before _upsert_player runs.
    _patch_identity(
        monkeypatch,
        AnniIdentity(
            linked=True, disc_uuid="123",
            mc_uuid="00000000-0000-0000-0000-000000000001",
            mc_username="Newbie", tier="other",
            blocked=False, reason=None,
        ),
    )
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "hard")

    assert "no anni" in outcome.private_message.lower()
    assert outcome.public_message is None
    # Defensive: ensure we did not create an orphan AnniPlayer.
    assert await AnniPlayer.filter(mc_uuid="00000000-0000-0000-0000-000000000001").count() == 0


async def test_execute_hard_writes_rsvp_and_announces(seeded, monkeypatch):
    baz = seeded["players"]["baz"]
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "hard")

    assert "HARD" in outcome.private_message
    assert outcome.public_message is not None
    assert "baz" in outcome.public_message
    assert "HARD" in outcome.public_message
    # DB shape: one active hard row exists for this player.
    event = await get_active_event()
    row = await Rsvp.filter(event=event, player=baz, revoked_at__isnull=True).first()
    assert row is not None and row.notice is AttendanceNotice.RSVP_HARD


async def test_execute_soft_writes_rsvp_and_announces(seeded, monkeypatch):
    baz = seeded["players"]["baz"]
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "soft")

    assert "SOFT" in outcome.private_message
    assert outcome.public_message is not None and "SOFT" in outcome.public_message
    event = await get_active_event()
    row = await Rsvp.filter(event=event, player=baz, revoked_at__isnull=True).first()
    assert row is not None and row.notice is AttendanceNotice.RSVP_SOFT


async def test_execute_revoke_announces_only_when_something_changed(seeded, monkeypatch):
    wen = seeded["players"]["Wenweia"]  # seeded with HARD
    _patch_identity(monkeypatch, _ident(wen, tier="member"))
    from app.bot.cogs.rsvp import execute_rsvp

    # First revoke: a real change → public line fires.
    outcome = await execute_rsvp(123, "revoke")
    assert outcome.public_message is not None
    assert "withdrew" in outcome.public_message.lower()

    # Second revoke: nothing to withdraw → silent (no spam).
    outcome = await execute_rsvp(123, "revoke")
    assert outcome.public_message is None
    assert "no active rsvp" in outcome.private_message.lower()


async def test_execute_status_never_announces(seeded, monkeypatch):
    wen = seeded["players"]["Wenweia"]
    _patch_identity(monkeypatch, _ident(wen, tier="member"))
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "status")

    # Wenweia has a HARD RSVP in the seed.
    assert "HARD" in outcome.private_message
    assert outcome.public_message is None  # status is read-only, never public.


async def test_execute_status_with_no_rsvp(seeded, monkeypatch):
    baz = seeded["players"]["baz"]
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "status")

    assert "no active rsvp" in outcome.private_message.lower()
    assert outcome.public_message is None


async def test_execute_creates_new_player_with_tier_from_dazebot(db, monkeypatch):
    # Make a fresh event so we don't depend on the seed. Stamp it well past the
    # T-90 RSVP cutoff so the gate doesn't refuse the call before player
    # upsert.
    from app.db.models import AnniEvent
    event = await AnniEvent.create(
        stamp_epoch=int(_time.time()) + 4 * 3600, is_active=True
    )

    new_uuid = "11111111-2222-3333-4444-555555555555"
    _patch_identity(
        monkeypatch,
        AnniIdentity(
            linked=True, disc_uuid="555", mc_uuid=new_uuid,
            mc_username="FreshUser", tier="honourary",  # dazebot-only signal
            blocked=False, reason=None,
        ),
    )
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(555, "hard")

    assert outcome.public_message is not None
    player = await AnniPlayer.get(mc_uuid=new_uuid)
    assert player.mc_username == "FreshUser"
    # The dazebot tier is the only place HONOURARY becomes visible to us.
    assert player.membership_tier is MembershipTier.HONOURARY
    assert await Rsvp.filter(event=event, player=player).count() == 1


async def test_execute_refreshes_mc_username_on_rename(seeded, monkeypatch):
    """If dazebot reports a new mc_username we adopt it (rename desync)."""
    pasta = seeded["players"]["_akaPasta"]
    assert pasta.mc_username == "_akaPasta"
    _patch_identity(
        monkeypatch,
        AnniIdentity(
            linked=True, disc_uuid="42", mc_uuid=pasta.mc_uuid,
            mc_username="NewPastaName", tier="member",
            blocked=False, reason=None,
        ),
    )
    from app.bot.cogs.rsvp import execute_rsvp

    await execute_rsvp(42, "hard")

    refreshed = await AnniPlayer.get(mc_uuid=pasta.mc_uuid)
    assert refreshed.mc_username == "NewPastaName"


# --------------------------------------------------------------------------- #
# T-90 RSVP cutoff — user-facing only; staff override and revoke bypass it.   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("level", ["hard", "soft"])
async def test_execute_refuses_new_rsvp_after_t90_cutoff(seeded, monkeypatch, level):
    """Inside T-90 a brand-new ``\\rsvp hard``/``soft`` is refused with a
    redirect to the walk-in / late-arrival paths, and no DB write happens."""
    baz = seeded["players"]["baz"]  # baz has no seeded RSVP
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    event = seeded["event"]
    event.stamp_epoch = int(_time.time()) + 80 * 60  # T-80 = past the cutoff
    await event.save(update_fields=["stamp_epoch"])
    placements_before = await BoardPlacement.filter(event=event, player=baz).count()
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, level)

    assert "close" in outcome.private_message.lower()
    assert "90 minutes" in outcome.private_message
    assert outcome.public_message is None
    # No RSVP row was written and the board wasn't mutated as a side effect.
    assert await Rsvp.filter(event=event, player=baz).count() == 0
    assert (
        await BoardPlacement.filter(event=event, player=baz).count()
        == placements_before
    )


async def test_execute_refuses_rsvp_swap_after_t90_cutoff(seeded, monkeypatch):
    """A user with an existing HARD cannot downgrade/upgrade past T-90 — the
    gate blocks any declaration, not just brand-new ones."""
    wen = seeded["players"]["Wenweia"]  # seeded with HARD
    _patch_identity(monkeypatch, _ident(wen, tier="member"))
    event = seeded["event"]
    event.stamp_epoch = int(_time.time()) + 80 * 60
    await event.save(update_fields=["stamp_epoch"])
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "soft")

    assert "close" in outcome.private_message.lower()
    assert outcome.public_message is None
    # Existing HARD row is untouched.
    row = await Rsvp.filter(event=event, player=wen, revoked_at__isnull=True).first()
    assert row is not None and row.notice is AttendanceNotice.RSVP_HARD


async def test_execute_revoke_still_works_after_t90_cutoff(seeded, monkeypatch):
    """The gate only blocks intent-to-attend declarations — pulling out is
    always allowed (and the public withdrawal line still fires)."""
    wen = seeded["players"]["Wenweia"]
    _patch_identity(monkeypatch, _ident(wen, tier="member"))
    event = seeded["event"]
    event.stamp_epoch = int(_time.time()) + 80 * 60
    await event.save(update_fields=["stamp_epoch"])
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "revoke")

    assert outcome.public_message is not None
    assert "withdrew" in outcome.public_message.lower()
    assert await rsvp_domain.get_current(wen, event) is None


async def test_execute_status_still_works_after_t90_cutoff(seeded, monkeypatch):
    """Read-only ``status`` is never blocked — the user can still check what
    we have on file even after the cutoff."""
    wen = seeded["players"]["Wenweia"]
    _patch_identity(monkeypatch, _ident(wen, tier="member"))
    event = seeded["event"]
    event.stamp_epoch = int(_time.time()) + 80 * 60
    await event.save(update_fields=["stamp_epoch"])
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "status")

    assert "HARD" in outcome.private_message  # seeded
    assert "close" not in outcome.private_message.lower()


# --------------------------------------------------------------------------- #
# discord.py shim — defer + ephemeral followup + conditional public post      #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeAuthor:
    id: int = 123


@dataclass
class _FakeContext:
    """Stand-in for ``commands.Context``: deferral + reply + author.id.

    The cog is a hybrid command now, so it talks to ``Context`` rather
    than ``Interaction``. Slash and prefix invocations share this surface.
    """

    author: _FakeAuthor = field(default_factory=_FakeAuthor)
    deferred_with: dict | None = None
    replies: list[tuple[str, bool]] = field(default_factory=list)

    async def defer(self, *, ephemeral: bool = False):
        self.deferred_with = {"ephemeral": ephemeral}

    async def reply(self, content: str, *, ephemeral: bool = False):
        self.replies.append((content, ephemeral))


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


class _FakeBot:
    """Minimal stand-in for ``commands.Bot`` exposing only ``get_channel``."""

    def __init__(self, channel: _FakeChannel | None = None) -> None:
        self._channel = channel

    def get_channel(self, _id: int):
        return self._channel


async def test_handle_defers_ephemerally_and_replies(seeded, monkeypatch):
    """Happy path: defer ephemeral + ephemeral followup + public-channel post."""
    baz = seeded["players"]["baz"]
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    # Pin a known channel id and a fake channel that captures the public line.
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "rsvp_channel_id", 999_999)
    channel = _FakeChannel()
    bot = _FakeBot(channel=channel)

    from app.bot.cogs.rsvp import RsvpCog
    cog = RsvpCog(bot)  # type: ignore[arg-type]
    ctx = _FakeContext()

    await cog._handle(ctx, "hard")  # type: ignore[arg-type]

    assert ctx.deferred_with == {"ephemeral": True}
    assert len(ctx.replies) == 1
    msg, ephemeral = ctx.replies[0]
    assert ephemeral is True and "HARD" in msg
    assert len(channel.sent) == 1 and "baz" in channel.sent[0]


# --------------------------------------------------------------------------- #
# \rsvp list / \rsvp check — read-only public subcommands (no dazebot needed) #
# --------------------------------------------------------------------------- #


async def test_set_public_message_uses_discord_timestamp_tag(seeded, monkeypatch):
    """Regression for the timezone-safety rewrite: the public echo must
    reference the anni via a Discord ``<t:N:R>`` tag, never English
    ("tonight", "today", etc.)."""
    baz = seeded["players"]["baz"]
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.bot.cogs.rsvp import execute_rsvp

    outcome = await execute_rsvp(123, "hard")

    event = seeded["event"]
    assert outcome.public_message is not None
    assert f"<t:{event.stamp_epoch}:R>" in outcome.public_message
    assert f"<t:{event.stamp_epoch}:F>" in outcome.public_message
    assert "tonight" not in outcome.public_message.lower()
    assert "today" not in outcome.public_message.lower()


async def test_execute_list_no_event(db):
    from app.bot.cogs.rsvp import execute_list

    msg = await execute_list()
    assert "No anni is currently announced" in msg


async def test_execute_list_splits_hard_and_soft(seeded):
    """The seed has 4 HARD (Wenweia, Nazzae, Metrafish, foo) and 2 SOFT
    (Trixomaniac, Paradrex) active RSVPs."""
    from app.bot.cogs.rsvp import execute_list

    msg = await execute_list()

    assert "**Hard (4):**" in msg
    assert "**Soft (2):**" in msg
    for name in ("Wenweia", "Nazzae", "Metrafish", "foo"):
        assert f"`{name}`" in msg
    for name in ("Trixomaniac", "Paradrex"):
        assert f"`{name}`" in msg
    # Discord timestamp tag in the header — never wall-clock English.
    event = seeded["event"]
    assert f"<t:{event.stamp_epoch}:R>" in msg


async def test_execute_list_empty_group_says_nobody_yet(seeded):
    """Revoke every HARD; the Hard line then reads "_nobody yet_"."""
    from app.bot.cogs.rsvp import execute_list

    event = seeded["event"]
    await Rsvp.filter(
        event=event, notice=AttendanceNotice.RSVP_HARD,
    ).update(revoked_at=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ))

    msg = await execute_list()
    assert "**Hard (0):** _nobody yet_" in msg
    # Soft RSVPs untouched.
    assert "**Soft (2):**" in msg


async def test_execute_check_unknown_name(seeded):
    """A name nobody knows -> friendly miss, no exception, no DB row."""
    from app.bot.cogs.rsvp import execute_check
    from app.services.state import AppState

    msg = await execute_check("NotARealPlayer", AppState())
    assert "don't know" in msg.lower()
    assert "NotARealPlayer" in msg


async def test_execute_check_empty_username(seeded):
    from app.bot.cogs.rsvp import execute_check
    from app.services.state import AppState

    msg = await execute_check("   ", AppState())
    assert "Specify an in-game name" in msg


async def test_execute_check_known_player_full_readout(seeded):
    """Wenweia is HARD'd, on Party 1 with role PRIMARY, in Returners (MEMBER)."""
    from app.bot.cogs.rsvp import execute_check
    from app.services.state import AppState

    msg = await execute_check("Wenweia", AppState())

    assert "Wenweia" in msg
    assert "Member" in msg                       # tier label
    assert "Core" in msg                         # has capabilities
    assert "Primary DPS" in msg                  # capability surfaced
    assert "RSVP: **HARD**" in msg               # active RSVP
    assert "Party 1" in msg                      # board placement
    assert "Dashboard:" in msg


async def test_execute_check_is_case_insensitive(seeded):
    from app.bot.cogs.rsvp import execute_check
    from app.services.state import AppState

    msg = await execute_check("WENWEIA", AppState())
    assert "Wenweia" in msg
    assert "RSVP: **HARD**" in msg


async def test_execute_check_no_capabilities_says_fill(seeded):
    """baz has no capability rows → Fill, no roles listed."""
    from app.bot.cogs.rsvp import execute_check
    from app.services.state import AppState

    msg = await execute_check("baz", AppState())
    assert "Fill" in msg
    assert "no capabilities declared" in msg


async def test_execute_check_api_disabled_player_says_unknown(seeded):
    """Metrafish has ``last_online == EPOCH`` (API-disabled). Online line
    must say "unknown" — never fabricate offline (CLAUDE.md hard rule)."""
    from app.bot.cogs.rsvp import execute_check
    from app.services.state import AppState

    msg = await execute_check("Metrafish", AppState())
    assert "unknown" in msg.lower()
    assert "API disabled" in msg


async def test_execute_check_resolves_via_alias_cache(seeded):
    """A legacy/in-game name only present in the alias cache still
    resolves to the right player (rename-desync path)."""
    from app.bot.cogs.rsvp import execute_check
    from app.services.state import AppState

    pasta = seeded["players"]["_akaPasta"]
    state = AppState()
    state.aliases["isnortpasta"] = pasta.mc_uuid  # how online_merge stores them

    msg = await execute_check("ISnortPasta", state)
    assert "_akaPasta" in msg
    # Rename-desync line shows the stale name.
    assert "ISnortPasta" in msg


async def test_handle_skips_public_post_when_channel_unset(seeded, monkeypatch):
    """A misconfigured RSVP_CHANNEL_ID is a logged warning, never a crash."""
    wen = seeded["players"]["Wenweia"]
    _patch_identity(monkeypatch, _ident(wen, tier="member"))
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "rsvp_channel_id", None)
    bot = _FakeBot(channel=None)

    from app.bot.cogs.rsvp import RsvpCog
    cog = RsvpCog(bot)  # type: ignore[arg-type]
    ctx = _FakeContext()

    await cog._handle(ctx, "revoke")  # type: ignore[arg-type]

    # Reply still went out — only the public side is silenced.
    assert len(ctx.replies) == 1
    assert ctx.replies[0][1] is True  # ephemeral


# --------------------------------------------------------------------------- #
# \rsvp set <target> <hard|soft> — staff override                             #
# --------------------------------------------------------------------------- #
#
# Decision tree: ``execute_rsvp_set`` mirrors ``execute_rsvp`` in shape — every
# input returns an ``RsvpOutcome``, never raises. The two-path resolution
# (Discord first, IGN fallback) is tested both ways. Gating is tested via the
# extracted ``_is_staff_predicate``; the cog ``_handle_set`` is exercised at
# the shim level (gating is on the ``set_`` subcommand itself, which only
# discord.py's command machinery would invoke).


@dataclass
class _FakeMember:
    """Minimal stand-in for ``discord.Member`` (what ``_resolve_member`` returns)."""

    id: int = 555
    display_name: str = "SomeMember"


@dataclass
class _FakeRole:
    id: int = 0


@dataclass
class _FakeGuild:
    id: int = 42


def _patch_member(monkeypatch, member: "_FakeMember | None") -> None:
    """Replace the cog's MemberConverter wrapper with a fixed result.

    Mirrors :func:`_patch_identity` — patches the name as the cog imported
    it so the actual ``commands.MemberConverter`` is never invoked (it
    would introspect ctx for things our fake doesn't carry).
    """
    from app.bot.cogs import rsvp as cog

    async def _fake(_ctx, _raw):
        return member

    monkeypatch.setattr(cog, "_resolve_member", _fake)


async def test_execute_set_no_active_event(db, monkeypatch):
    """No AnniEvent → friendly miss, never reaches resolution."""
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    outcome = await execute_rsvp_set(
        _FakeContext(), "irrelevant", "hard", AppState()  # type: ignore[arg-type]
    )

    assert "no anni" in outcome.private_message.lower()
    assert outcome.public_message is None
    # No DB write, no AnniPlayer created.
    assert await Rsvp.all().count() == 0


async def test_execute_set_discord_member_happy_path_hard(seeded, monkeypatch):
    """Discord member resolves → dazebot identity → HARD RSVP + [Override] line."""
    baz = seeded["players"]["baz"]
    _patch_member(monkeypatch, _FakeMember(id=555, display_name="BazDiscord"))
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    outcome = await execute_rsvp_set(
        _FakeContext(), "@BazDiscord", "hard", AppState()  # type: ignore[arg-type]
    )

    assert "Override recorded" in outcome.private_message
    assert "baz" in outcome.private_message
    assert "HARD" in outcome.private_message
    assert outcome.public_message is not None
    assert outcome.public_message.startswith("[Override]")
    assert "baz" in outcome.public_message
    assert "HARD" in outcome.public_message
    assert "RSVP'd manually by staff" in outcome.public_message
    assert "\\rsvp" in outcome.public_message  # nudge to self-RSVP next time

    event = await get_active_event()
    row = await Rsvp.filter(
        event=event, player=baz, revoked_at__isnull=True
    ).first()
    assert row is not None and row.notice is AttendanceNotice.RSVP_HARD


async def test_execute_set_discord_member_happy_path_soft(seeded, monkeypatch):
    """level='soft' stores ``AttendanceNotice.RSVP_SOFT``."""
    baz = seeded["players"]["baz"]
    _patch_member(monkeypatch, _FakeMember(id=555, display_name="BazDiscord"))
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    outcome = await execute_rsvp_set(
        _FakeContext(), "@BazDiscord", "soft", AppState()  # type: ignore[arg-type]
    )

    assert "SOFT" in outcome.private_message
    assert outcome.public_message is not None and "SOFT" in outcome.public_message
    event = await get_active_event()
    row = await Rsvp.filter(
        event=event, player=baz, revoked_at__isnull=True
    ).first()
    assert row is not None and row.notice is AttendanceNotice.RSVP_SOFT


async def test_execute_set_discord_member_not_linked(seeded, monkeypatch):
    """Discord member with no dazebot link → friendly error, no DB write."""
    _patch_member(monkeypatch, _FakeMember(id=555, display_name="Newbie"))
    _patch_identity(
        monkeypatch,
        AnniIdentity(
            linked=False, disc_uuid="555", mc_uuid=None, mc_username=None,
            tier=None, blocked=False, reason="no linked minecraft account",
        ),
    )
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    before = await Rsvp.all().count()
    outcome = await execute_rsvp_set(
        _FakeContext(), "@Newbie", "hard", AppState()  # type: ignore[arg-type]
    )

    assert "no Minecraft account linked" in outcome.private_message
    assert "Newbie" in outcome.private_message
    assert outcome.public_message is None
    assert await Rsvp.all().count() == before


async def test_execute_set_discord_member_blocked(seeded, monkeypatch):
    """Discord member resolved but link is blocked → friendly error, no write."""
    baz = seeded["players"]["baz"]
    _patch_member(monkeypatch, _FakeMember(id=555, display_name="BadGuy"))
    _patch_identity(monkeypatch, _ident(baz, blocked=True, reason="griefing"))
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    before = await Rsvp.all().count()
    outcome = await execute_rsvp_set(
        _FakeContext(), "@BadGuy", "hard", AppState()  # type: ignore[arg-type]
    )

    assert "blocked" in outcome.private_message.lower()
    assert "griefing" in outcome.private_message
    assert outcome.public_message is None
    assert await Rsvp.all().count() == before


async def test_execute_set_dazebot_unavailable(seeded, monkeypatch):
    """Discord member resolved but dazebot is down → friendly error, no write."""
    _patch_member(monkeypatch, _FakeMember(id=555, display_name="Someone"))
    _patch_identity(monkeypatch, None)
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    before = await Rsvp.all().count()
    outcome = await execute_rsvp_set(
        _FakeContext(), "@Someone", "hard", AppState()  # type: ignore[arg-type]
    )

    assert "identity service is unavailable" in outcome.private_message.lower()
    assert outcome.public_message is None
    assert await Rsvp.all().count() == before


async def test_execute_set_ign_fallback_hits(seeded, monkeypatch):
    """No Discord match → IGN lookup succeeds → RSVP recorded."""
    _patch_member(monkeypatch, None)  # MemberConverter would have failed
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    outcome = await execute_rsvp_set(
        _FakeContext(), "Wenweia", "soft", AppState()  # type: ignore[arg-type]
    )

    assert "Override recorded" in outcome.private_message
    assert "Wenweia" in outcome.private_message
    assert "SOFT" in outcome.private_message
    assert outcome.public_message is not None
    assert "[Override]" in outcome.public_message
    assert "Wenweia" in outcome.public_message

    wen = seeded["players"]["Wenweia"]
    event = await get_active_event()
    row = await Rsvp.filter(
        event=event, player=wen, revoked_at__isnull=True
    ).first()
    assert row is not None and row.notice is AttendanceNotice.RSVP_SOFT


async def test_execute_set_both_paths_miss(seeded, monkeypatch):
    """No Discord match + no IGN match → friendly error, no DB change."""
    _patch_member(monkeypatch, None)
    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    before = await Rsvp.all().count()
    outcome = await execute_rsvp_set(
        _FakeContext(), "TotallyMadeUpName", "hard", AppState()  # type: ignore[arg-type]
    )

    assert "can't find" in outcome.private_message.lower()
    assert "TotallyMadeUpName" in outcome.private_message
    assert outcome.public_message is None
    assert await Rsvp.all().count() == before


# --- _is_staff predicate ---------------------------------------------------- #


@dataclass
class _FakeAuthorWithRoles:
    id: int = 1
    roles: list = field(default_factory=list)


@dataclass
class _FakeCtxWithGuild:
    author: _FakeAuthorWithRoles = field(default_factory=_FakeAuthorWithRoles)
    guild: object | None = None


async def test_is_staff_predicate_passes_with_role():
    from app.bot.cogs.rsvp import _is_staff_predicate
    from app.settings import get_settings

    role_id = get_settings().staff_role_id
    ctx = _FakeCtxWithGuild(
        author=_FakeAuthorWithRoles(roles=[_FakeRole(id=role_id)]),
        guild=_FakeGuild(),
    )
    assert await _is_staff_predicate(ctx) is True  # type: ignore[arg-type]


async def test_is_staff_predicate_fails_without_role():
    from app.bot.cogs.rsvp import _is_staff_predicate

    ctx = _FakeCtxWithGuild(
        author=_FakeAuthorWithRoles(roles=[_FakeRole(id=1)]),
        guild=_FakeGuild(),
    )
    assert await _is_staff_predicate(ctx) is False  # type: ignore[arg-type]


async def test_is_staff_predicate_fails_in_dm():
    """Role membership is a guild concept — no guild means no staff."""
    from app.bot.cogs.rsvp import _is_staff_predicate
    from app.settings import get_settings

    role_id = get_settings().staff_role_id
    ctx = _FakeCtxWithGuild(
        author=_FakeAuthorWithRoles(roles=[_FakeRole(id=role_id)]),
        guild=None,
    )
    assert await _is_staff_predicate(ctx) is False  # type: ignore[arg-type]


async def test_is_staff_predicate_honors_settings_override(monkeypatch):
    """Tests can change the staff role at runtime via monkeypatch."""
    from app.bot.cogs.rsvp import _is_staff_predicate
    from app.settings import get_settings

    monkeypatch.setattr(get_settings(), "staff_role_id", 99_999)
    ctx = _FakeCtxWithGuild(
        author=_FakeAuthorWithRoles(roles=[_FakeRole(id=99_999)]),
        guild=_FakeGuild(),
    )
    assert await _is_staff_predicate(ctx) is True  # type: ignore[arg-type]


# --- cog shim _handle_set --------------------------------------------------- #


async def test_handle_set_defers_replies_and_posts_override(seeded, monkeypatch):
    """Happy path: defer ephemeral + ephemeral private reply + public override post."""
    baz = seeded["players"]["baz"]
    _patch_member(monkeypatch, _FakeMember(id=555, display_name="BazDiscord"))
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "rsvp_channel_id", 999_999)
    channel = _FakeChannel()
    bot = _FakeBot(channel=channel)

    from app.bot.cogs.rsvp import RsvpCog
    cog = RsvpCog(bot)  # type: ignore[arg-type]
    ctx = _FakeContext()

    await cog._handle_set(ctx, "@BazDiscord", "hard")  # type: ignore[arg-type]

    assert ctx.deferred_with == {"ephemeral": True}
    assert len(ctx.replies) == 1
    msg, ephemeral = ctx.replies[0]
    assert ephemeral is True
    assert "Override recorded" in msg
    assert len(channel.sent) == 1
    assert channel.sent[0].startswith("[Override]")
    assert "baz" in channel.sent[0]


async def test_handle_set_no_public_post_on_failure(seeded, monkeypatch):
    """A resolution miss replies ephemerally and skips the public channel."""
    _patch_member(monkeypatch, None)  # both paths miss
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "rsvp_channel_id", 999_999)
    channel = _FakeChannel()
    bot = _FakeBot(channel=channel)

    from app.bot.cogs.rsvp import RsvpCog
    cog = RsvpCog(bot)  # type: ignore[arg-type]
    ctx = _FakeContext()

    await cog._handle_set(ctx, "NoSuchPerson", "hard")  # type: ignore[arg-type]

    assert len(ctx.replies) == 1 and ctx.replies[0][1] is True
    assert "can't find" in ctx.replies[0][0].lower()
    assert channel.sent == []  # nothing public on a miss


# ---------------------------------------------------------------------------
# Auto-place side effects (spec.md "auto-populated from RSVP or 1hr-early"). #
# These cover the wiring between ``_do_set`` / ``_do_revoke`` and the board: #
# every RSVP that's accepted MUST result in a BoardPlacement; every revoke   #
# MUST either demote out of Unassigned or leave the placement intact so the  #
# view-layer "Retracted" pill can surface the state.                         #
# ---------------------------------------------------------------------------
from app.constants import BucketKind


async def test_execute_hard_creates_board_placement(seeded, monkeypatch):
    """``/rsvp hard`` lands the user in Unassigned immediately.

    Holidaze is the seed's "organiser, intentionally not placed" — the only
    seeded player with no existing BoardPlacement, so a fresh RSVP is the
    *first* row for them and we can assert its shape directly.
    """
    holidaze = seeded["players"]["Holidaze"]
    _patch_identity(monkeypatch, _ident(holidaze, tier="member"))
    # Pin the event far enough in the future that we're in the EARLY lane.
    event = seeded["event"]
    event.stamp_epoch = int(_time.time()) + 4 * 3600
    await event.save(update_fields=["stamp_epoch"])

    assert await BoardPlacement.filter(event=event, player=holidaze).count() == 0

    from app.bot.cogs.rsvp import execute_rsvp
    await execute_rsvp(123, "hard")

    placed = await BoardPlacement.get(event=event, player=holidaze)
    assert placed.bucket is BucketKind.UNASSIGNED
    assert placed.is_late is False


async def test_staff_override_inside_late_window_still_uses_main_lane(seeded, monkeypatch):
    """At T-30min the user-facing /rsvp gate has long since slammed shut
    (T-90), but a staff override still lands the player on the board. An
    RSVP'd user — even from a staff override past T-60 — always lands in
    the main Unassigned lane: walk-in and LATE are exclusively for
    non-RSVP'd auto-detected arrivals, so an explicit RSVP (even a late
    one) is rendered as a normal commit, not a walk-in / late arrival."""
    holidaze = seeded["players"]["Holidaze"]
    _patch_member(monkeypatch, _FakeMember(id=555, display_name="HolidazeDiscord"))
    _patch_identity(monkeypatch, _ident(holidaze, tier="member"))
    event = seeded["event"]
    event.stamp_epoch = int(_time.time()) + 30 * 60  # T-30 — past the LATE switch
    await event.save(update_fields=["stamp_epoch"])

    from app.bot.cogs.rsvp import execute_rsvp_set
    from app.services.state import AppState

    outcome = await execute_rsvp_set(
        _FakeContext(), "@HolidazeDiscord", "hard", AppState()  # type: ignore[arg-type]
    )
    assert "Override recorded" in outcome.private_message

    placed = await BoardPlacement.get(event=event, player=holidaze)
    assert placed.bucket is BucketKind.UNASSIGNED
    assert placed.is_late is False
    assert placed.is_walkin is False


async def test_execute_revoke_demotes_from_unassigned_to_wontassign(seeded, monkeypatch):
    """A user revoking their RSVP from the Unassigned bucket hops to
    Won't-assign — staff don't have to clean it up manually."""
    holidaze = seeded["players"]["Holidaze"]
    _patch_identity(monkeypatch, _ident(holidaze, tier="member"))
    event = seeded["event"]
    event.stamp_epoch = int(_time.time()) + 4 * 3600
    await event.save(update_fields=["stamp_epoch"])

    from app.bot.cogs.rsvp import execute_rsvp
    await execute_rsvp(123, "hard")
    # Confirm setup.
    placed = await BoardPlacement.get(event=event, player=holidaze)
    assert placed.bucket is BucketKind.UNASSIGNED

    await execute_rsvp(123, "revoke")
    placed = await BoardPlacement.get(event=event, player=holidaze)
    assert placed.bucket is BucketKind.WONTASSIGN


async def test_execute_revoke_keeps_party_placement(seeded, monkeypatch):
    """A user in a party slot stays put on revoke (staff intent wins); only
    the view layer surfaces the retraction via the Retracted pill."""
    wen = seeded["players"]["Wenweia"]  # seeded into a party with HARD RSVP
    _patch_identity(monkeypatch, _ident(wen, tier="member"))

    from app.bot.cogs.rsvp import execute_rsvp
    before = await BoardPlacement.get(event=seeded["event"], player=wen)
    party_id = before.party_id

    await execute_rsvp(123, "revoke")

    after = await BoardPlacement.get(event=seeded["event"], player=wen)
    assert after.party_id == party_id  # still in the same party
    assert after.bucket is None


async def test_rsvp_clears_placeholder_on_existing_player(seeded, monkeypatch):
    """When a placeholder user RSVPs via dazebot, their AnniPlayer row is
    upgraded out of the placeholder state (silent swap on next render)."""
    from app.bot.cogs.rsvp import _upsert_player
    from app.services.dazebot_client import AnniIdentity

    stub = await AnniPlayer.create(
        mc_uuid="uuid-stubrsvp", mc_username="StubRsvp", is_placeholder=True,
    )
    ident = AnniIdentity(
        linked=True, disc_uuid="d", mc_uuid=stub.mc_uuid,
        mc_username="StubRsvp", tier="other", blocked=False, reason=None,
    )

    p = await _upsert_player(ident)
    assert p.is_placeholder is False
    # Persisted, not just in-memory.
    await p.refresh_from_db()
    assert p.is_placeholder is False
