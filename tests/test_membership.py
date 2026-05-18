"""Membership tiering — the case-sensitive ally rule + priority resolution.

The ally-tag exact-match is a deliberate, documented decision (Wynn tags are
case-sensitive and non-unique); regressing it would silently mis-tier allies.
"""

from __future__ import annotations

from app.constants import MembershipTier as M
from app.domain import membership

ALLY = {"TCM", "SSNE"}
RET = "Returners"


def test_from_guild_basics():
    assert membership.from_guild(guild_name=RET, guild_tag="VETS",
                                 ally_tags=ALLY, returners_guild_name=RET) is M.MEMBER
    assert membership.from_guild(guild_name=None, guild_tag=None,
                                 ally_tags=ALLY, returners_guild_name=RET) is M.COMMUNITY
    assert membership.from_guild(guild_name="Team CM", guild_tag="TCM",
                                 ally_tags=ALLY, returners_guild_name=RET) is M.ALLY
    assert membership.from_guild(guild_name="Wynn", guild_tag="WYNN",
                                 ally_tags=ALLY, returners_guild_name=RET) is M.OTHER


def test_ally_tag_match_is_case_sensitive():
    # 'tcm' != 'TCM' on Wynncraft -> NOT an ally, falls to OTHER.
    assert membership.from_guild(guild_name="x", guild_tag="tcm",
                                 ally_tags=ALLY, returners_guild_name=RET) is M.OTHER


def test_resolve_highest_priority_signal_wins():
    # Roster membership (authoritative MEMBER) beats a non-Returners guild.
    assert membership.resolve(
        in_returners_roster=True, dazebot_tier=None, guild_name="Wynn",
        guild_tag="WYNN", ally_tags=ALLY, returners_guild_name=RET) is M.MEMBER
    # dazebot honourary beats a guildless COMMUNITY (priority 2 < 3).
    assert membership.resolve(
        in_returners_roster=False, dazebot_tier="honourary", guild_name=None,
        guild_tag=None, ally_tags=ALLY, returners_guild_name=RET) is M.HONOURARY
    # Nothing special -> the guild-derived tier.
    assert membership.resolve(
        in_returners_roster=False, dazebot_tier=None, guild_name="Team CM",
        guild_tag="TCM", ally_tags=ALLY, returners_guild_name=RET) is M.ALLY


def test_label_is_defined_for_every_tier():
    for tier in M:
        assert membership.label(tier)
