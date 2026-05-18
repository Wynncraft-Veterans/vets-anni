"""Membership tiering — *which* tier a player counts as for prioritisation.

Pure: every input is passed in (the caller does the WAPI/dazebot I/O). Rules
(spec.md "Registration Status" + ``.claude/domain_rules.md``):

* **MEMBER**    — in the Returners guild (roster hit, or guild name match).
* **WAITLIST / HONOURARY** — only dazebot can assert these (Discord-linked);
  the Phase-1 web login never sees them, fishbot (Phase 3) supplies them.
* **COMMUNITY** — guildless.
* **ALLY**      — guild *tag* in ``ally_tags`` (exact, case-sensitive — Wynn
  tags are case-sensitive and NOT unique; safe here because tiering is only
  ever evaluated for RSVP'd players and the colliding guilds are inactive).
* **OTHER**     — any other guild.

Several signals can apply at once (a Returners member who also has the
dazebot honourary role); we collect every candidate and return the
highest-priority one via ``MEMBERSHIP_PRIORITY``.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.constants import MEMBERSHIP_PRIORITY, MembershipTier

#: dazebot tier string -> our enum (the anni-identity endpoint, Phase 3).
_DAZEBOT_TIER: dict[str, MembershipTier] = {
    "member": MembershipTier.MEMBER,
    "waitlist": MembershipTier.WAITLIST,
    "honourary": MembershipTier.HONOURARY,
}


def from_guild(
    *,
    guild_name: str | None,
    guild_tag: str | None,
    ally_tags: Iterable[str],
    returners_guild_name: str,
) -> MembershipTier:
    """Tier implied purely by the player's current guild.

    ``guild_tag`` is compared **exactly** (case-sensitive) against
    ``ally_tags`` — see the module docstring.
    """
    if guild_name and guild_name.strip() == returners_guild_name:
        return MembershipTier.MEMBER
    if not guild_name:
        return MembershipTier.COMMUNITY
    if guild_tag is not None and guild_tag in set(ally_tags):
        return MembershipTier.ALLY
    return MembershipTier.OTHER


def resolve(
    *,
    in_returners_roster: bool,
    dazebot_tier: str | None,
    guild_name: str | None,
    guild_tag: str | None,
    ally_tags: Iterable[str],
    returners_guild_name: str,
) -> MembershipTier:
    """The effective tier from every available signal (highest priority wins).

    ``in_returners_roster`` is the temp-server Returners roster membership
    (authoritative for MEMBER without spending our token). ``dazebot_tier``
    is ``None`` on the web path (Phase 1) and the dazebot string on the
    fishbot path (Phase 3).
    """
    candidates: list[MembershipTier] = []
    if in_returners_roster:
        candidates.append(MembershipTier.MEMBER)
    if dazebot_tier and (mapped := _DAZEBOT_TIER.get(dazebot_tier.lower())):
        candidates.append(mapped)
    candidates.append(
        from_guild(
            guild_name=guild_name,
            guild_tag=guild_tag,
            ally_tags=ally_tags,
            returners_guild_name=returners_guild_name,
        )
    )
    # Lowest MEMBERSHIP_PRIORITY number == highest priority.
    return min(candidates, key=lambda t: MEMBERSHIP_PRIORITY[t])


#: Human label per tier (dashboards / pills).
TIER_LABEL: dict[MembershipTier, str] = {
    MembershipTier.MEMBER: "Member (In-VETS)",
    MembershipTier.WAITLIST: "Member (Waitlisted)",
    MembershipTier.HONOURARY: "Member (Honourary)",
    MembershipTier.COMMUNITY: "Community (Guildless)",
    MembershipTier.ALLY: "Community (Ally)",
    MembershipTier.OTHER: "Community (Other)",
}


def label(tier: MembershipTier) -> str:
    return TIER_LABEL.get(tier, tier.value.title())
