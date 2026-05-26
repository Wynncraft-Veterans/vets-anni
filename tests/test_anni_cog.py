"""``\\anni`` group — public read-only views.

Mirrors the layering in ``test_rsvp.py``: pure ``execute_*`` calls run
against the seeded in-memory DB, plus a couple of cog-shim driven cases
with the same ``_FakeContext``/``_FakeBot`` pattern. No real Discord
interaction needed.

The seeded dataset (``scripts/seed_dev.populate``):
    * Event ``stamp = now + 93 min``, organiser ``Holidaze``.
    * Two parties — #1 hosted by Holidaze on world ``AS5`` at stage 3
      with three placements, #2 hosted by Nazzae at stage 1 with one.
    * Six active RSVPs (4 HARD / 2 SOFT).
    * Capability rows for Wenweia (PRIMARY+HEALER), Nazzae, _akaPasta,
      Paradrex.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.dazebot_client import AnniIdentity


# --------------------------------------------------------------------------- #
# execute_status / execute_parties — purely DB-driven                         #
# --------------------------------------------------------------------------- #


async def test_status_with_no_event(db):
    from app.bot.cogs.anni import execute_status

    msg = await execute_status()
    assert "No anni is currently announced" in msg
    assert "dashboard" in msg.lower()


async def test_status_renders_seeded_event(seeded):
    from app.bot.cogs.anni import execute_status

    event = seeded["event"]
    msg = await execute_status()

    # The Discord timestamp tag carries the stamp (per CLAUDE.md no
    # wall-clock English); both :F and :R variants land in the line.
    assert f"<t:{event.stamp_epoch}:F>" in msg
    assert f"<t:{event.stamp_epoch}:R>" in msg
    assert "Holidaze" in msg
    # 2 parties, 15 placements, 6 active RSVPs (see scripts/seed_dev.py).
    assert "Parties:** 2" in msg
    assert "On the board:** 15" in msg
    assert "Active RSVPs:** 6" in msg


async def test_status_never_says_tonight(seeded):
    """CLAUDE.md timezone rule: no wall-clock English for anni timing."""
    from app.bot.cogs.anni import execute_status

    msg = (await execute_status()).lower()
    for banned in ("tonight", "today", "tomorrow", "this evening"):
        assert banned not in msg, f"unexpected wall-clock word {banned!r}"


async def test_parties_with_no_event(db):
    from app.bot.cogs.anni import execute_parties

    msg = await execute_parties()
    assert "No anni is currently announced" in msg


async def test_parties_with_no_parties_yet(db):
    """Active event but zero parties — friendly "none yet" branch."""
    from app.bot.cogs.anni import execute_parties
    from app.db.models import AnniEvent

    await AnniEvent.create(stamp_epoch=10**9, is_active=True)
    msg = await execute_parties()
    assert "No parties created yet" in msg
    assert "<t:1000000000:F>" in msg


async def test_parties_lists_every_seeded_party(seeded):
    from app.bot.cogs.anni import execute_parties

    msg = await execute_parties()

    # Both ordinals appear.
    assert "Party 1" in msg and "Party 2" in msg
    # Stage 3 / Stage 1 from the seed.
    assert "Stage 3/5" in msg and "Stage 1/5" in msg
    # Hosts + the world that's set on party #1 only.
    assert "Holidaze" in msg and "Nazzae" in msg
    assert "AS5" in msg
    # Party #2 has no world yet — the placeholder must render.
    assert "world TBD" in msg
    # Membership counts: P1 has 3 placements, P2 has 1.
    assert "3/10" in msg and "1/10" in msg
    # The stage label from PARTY_STAGE_LABELS is appended on each line.
    assert "which core users go in which parties" in msg  # stage 3
    assert "hasn't started" in msg                         # stage 1


# --------------------------------------------------------------------------- #
# execute_roles — dazebot identity prelude + read-only fetch                  #
# --------------------------------------------------------------------------- #


def _patch_identity(monkeypatch, identity: AnniIdentity | None):
    """Replace the dazebot client lookup (same shape as ``test_rsvp``)."""
    class _Fake:
        async def resolve_anni_identity(self, _discord_id):
            return identity
    from app.bot.cogs import anni as cog
    monkeypatch.setattr(cog, "get_dazebot_client", lambda: _Fake())


def _ident(player, *, tier: str = "member", **overrides) -> AnniIdentity:
    base = dict(
        linked=True, disc_uuid="42", mc_uuid=player.mc_uuid,
        mc_username=player.mc_username, tier=tier,
        blocked=False, reason=None,
    )
    base.update(overrides)
    return AnniIdentity(**base)


async def test_roles_unavailable_when_dazebot_down(seeded, monkeypatch):
    _patch_identity(monkeypatch, None)
    from app.bot.cogs.anni import execute_roles

    msg = await execute_roles(123)
    assert "identity service is unavailable" in msg.lower()


async def test_roles_unlinked(seeded, monkeypatch):
    _patch_identity(
        monkeypatch,
        AnniIdentity(linked=False, disc_uuid="1", mc_uuid=None,
                     mc_username=None, tier=None, blocked=False, reason=None),
    )
    from app.bot.cogs.anni import execute_roles

    msg = await execute_roles(123)
    assert "verify" in msg.lower()


async def test_roles_blocked(seeded, monkeypatch):
    wen = seeded["players"]["Wenweia"]
    _patch_identity(monkeypatch, _ident(wen, blocked=True, reason="banned"))
    from app.bot.cogs.anni import execute_roles

    msg = await execute_roles(123)
    assert "blocked" in msg.lower()
    assert "banned" in msg


async def test_roles_no_capabilities_yet(seeded, monkeypatch):
    """A linked player with no RoleCapability rows shows the friendly empty
    branch, not a crash."""
    baz = seeded["players"]["baz"]  # baz has no capabilities seeded
    _patch_identity(monkeypatch, _ident(baz, tier="community"))
    from app.bot.cogs.anni import execute_roles

    msg = await execute_roles(123)
    assert "hasn't declared any role capabilities" in msg.lower()
    assert "/me" in msg


async def test_roles_happy_path_for_wenweia(seeded, monkeypatch):
    """Wenweia has PRIMARY (Labyrinth + Revolution) + HEALER (Lament)."""
    wen = seeded["players"]["Wenweia"]
    _patch_identity(monkeypatch, _ident(wen))
    from app.bot.cogs.anni import execute_roles

    msg = await execute_roles(123)
    # Header carries the player's name and a read-only marker.
    assert "Wenweia" in msg
    assert "read-only" in msg.lower()
    # Both seeded roles surface by their guidance title.
    assert "Primary DPS" in msg
    assert "Healer" in msg
    # Weapons listed.
    assert "Labyrinth" in msg and "Revolution" in msg and "Lament" in msg


# --------------------------------------------------------------------------- #
# Cog shim — defers + replies publicly (no ephemeral)                         #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeAuthor:
    id: int = 123


@dataclass
class _FakeContext:
    author: _FakeAuthor = field(default_factory=_FakeAuthor)
    deferred_with: dict | None = None
    replies: list[tuple[str, bool]] = field(default_factory=list)

    async def defer(self, *, ephemeral: bool = False):
        self.deferred_with = {"ephemeral": ephemeral}

    async def reply(self, content: str, *, ephemeral: bool = False):
        self.replies.append((content, ephemeral))


class _FakeBot:
    """``commands.Bot`` substitute — the anni cog only uses ``self.bot`` in
    handler bodies that don't touch it, but :class:`AnniCog` still needs
    *something* in its constructor."""


async def test_public_reply_is_not_ephemeral(seeded):
    """The shim defers non-ephemerally and forwards the rendered message
    verbatim. Driven directly against ``_public_reply`` because the
    ``@hybrid_group.command`` wrapper resists in-process invocation
    without the full discord.py machinery (the test layer ``test_rsvp.py``
    uses the same direct-shim pattern for the same reason)."""
    from app.bot.cogs.anni import AnniCog, execute_status

    cog = AnniCog(_FakeBot())  # type: ignore[arg-type]
    ctx = _FakeContext()
    rendered = await execute_status()
    await cog._public_reply(ctx, rendered)  # type: ignore[arg-type]

    assert ctx.deferred_with == {"ephemeral": False}
    assert len(ctx.replies) == 1
    msg, ephemeral = ctx.replies[0]
    assert ephemeral is False
    assert "Holidaze" in msg
