"""``/rsvp`` — pin the spec'd subcommand behaviour at every layer.

The slash command is the single user-visible point of fishbot, so the
test surface is wide on purpose:

* The pure :mod:`app.domain.rsvp` writer (single-instance, soft-delete,
  revive-on-rsvp, ATTEND_EARLY/ATTEND_LATE refused).
* The :func:`execute_rsvp` decision tree (every branch: dazebot down,
  not linked, blocked, no active event, happy paths, plus a brand-new
  fishbot user → AnniPlayer get-or-create with tier from dazebot).
* The discord.py shim ``RsvpCog._handle`` (defers + ephemeral reply +
  conditional public post) driven by a ``FakeInteraction``/``FakeBot``,
  mirroring how the WS layer is tested against the hub directly
  (CLAUDE.md Phase-2 decisions).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.constants import AttendanceNotice, MembershipTier
from app.db.lifecycle import get_active_event
from app.db.models import AnniPlayer, Rsvp
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
    # Make a fresh event so we don't depend on the seed.
    from app.db.models import AnniEvent
    event = await AnniEvent.create(stamp_epoch=10**9, is_active=True)

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
# discord.py shim — defer + ephemeral followup + conditional public post      #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeResponse:
    deferred_with: dict | None = None

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False):
        self.deferred_with = {"ephemeral": ephemeral, "thinking": thinking}


@dataclass
class _FakeFollowup:
    sent: list[tuple[str, bool]] = field(default_factory=list)

    async def send(self, content: str, *, ephemeral: bool = False):
        self.sent.append((content, ephemeral))


@dataclass
class _FakeUser:
    id: int = 123


@dataclass
class _FakeInteraction:
    user: _FakeUser = field(default_factory=_FakeUser)
    response: _FakeResponse = field(default_factory=_FakeResponse)
    followup: _FakeFollowup = field(default_factory=_FakeFollowup)


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
    interaction = _FakeInteraction()

    await cog._handle(interaction, "hard")  # type: ignore[arg-type]

    assert interaction.response.deferred_with == {"ephemeral": True, "thinking": True}
    assert len(interaction.followup.sent) == 1
    msg, ephemeral = interaction.followup.sent[0]
    assert ephemeral is True and "HARD" in msg
    assert len(channel.sent) == 1 and "baz" in channel.sent[0]


async def test_handle_skips_public_post_when_channel_unset(seeded, monkeypatch):
    """A misconfigured RSVP_CHANNEL_ID is a logged warning, never a crash."""
    wen = seeded["players"]["Wenweia"]
    _patch_identity(monkeypatch, _ident(wen, tier="member"))
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "rsvp_channel_id", None)
    bot = _FakeBot(channel=None)

    from app.bot.cogs.rsvp import RsvpCog
    cog = RsvpCog(bot)  # type: ignore[arg-type]
    interaction = _FakeInteraction()

    await cog._handle(interaction, "revoke")  # type: ignore[arg-type]

    # Followup still went out — only the public side is silenced.
    assert len(interaction.followup.sent) == 1
    assert interaction.followup.sent[0][1] is True  # ephemeral
