"""Presence state machine — the spec's status mapping + bar escalation.

The full six-status sweep with all thresholds is Phase 2's remit (alongside
the live poller); this pins the rules the Phase-1 user dashboard already
renders so they can't silently drift before then.
"""

from __future__ import annotations

from app.constants import AttendanceNotice as N
from app.constants import PresenceStatus as S
from app.domain import presence
from app.domain.presence import PresenceInputs as I


def test_queued_is_online_elsewhere_never_offline():
    # Anni is queue-heavy: queued == connecting, must not be OFFLINE_*.
    assert presence.classify(I(online=True, queued=True, has_party=True)) is S.ONLINE_ELSEWHERE


def test_online_world_vs_party_requires_world_match_and_confirmation():
    base = dict(online=True, has_party=True, party_world="WC1")
    assert presence.classify(I(**base, current_server="WC2")) is S.ONLINE_ELSEWHERE
    assert presence.classify(I(**base, current_server="WC1")) is S.ONLINE_WORLD
    assert presence.classify(
        I(**base, current_server="WC1", in_party_confirmed=True)
    ) is S.ONLINE_PARTY
    # Party assigned but no server signal (the common Phase-1 case).
    assert presence.classify(I(**base)) is S.ONLINE_ELSEWHERE


def test_world_comparison_is_format_tolerant():
    # WAPI returns "WC1"; staff often type "1" or "wc1" or "01". Without
    # normalisation the comparison falls through to ELSEWHERE and
    # ONLINE_WORLD is unreachable.
    base = dict(online=True, has_party=True)
    assert presence.classify(
        I(**base, party_world="1", current_server="WC1")
    ) is S.ONLINE_WORLD
    assert presence.classify(
        I(**base, party_world="wc1", current_server="WC1")
    ) is S.ONLINE_WORLD
    assert presence.classify(
        I(**base, party_world="01", current_server="WC1")
    ) is S.ONLINE_WORLD
    # Mismatched numeric worlds still mismatch.
    assert presence.classify(
        I(**base, party_world="1", current_server="WC2")
    ) is S.ONLINE_ELSEWHERE


def test_normalize_world():
    nw = presence.normalize_world
    assert nw("WC1") == "WC1"
    assert nw("wc1") == "WC1"
    assert nw("1") == "WC1"
    assert nw("01") == "WC1"
    assert nw(" WC 7 ") == "WC7"
    assert nw(None) is None
    assert nw("") is None
    assert nw("   ") is None
    # Non-numeric server names pass through uppercased (don't silently
    # match a numbered world).
    assert nw("lobby") == "LOBBY"
    assert nw("hub2") == "HUB2"


def test_offline_is_one_status_whatever_the_history_or_rsvp():
    """The border says where someone is RIGHT NOW and nothing else. Neither
    what they promised (``RsvpState``, its own axis) nor whether they were
    here earlier (``board_view``'s is_gone badge, another) splits it."""
    for was_online in (False, True):
        for notice in (N.RSVP_HARD, N.RSVP_SOFT, None):
            assert presence.classify(
                I(rsvp_notice=notice, was_online=was_online)
            ) is S.OFFLINE
    assert not hasattr(S, "OFFLINE_GONE")   # retired, not renamed


def test_api_disabled_offline_is_unknown_but_online_merge_confirms():
    # Can't confirm -> UNKNOWN even with a hard RSVP (never faked online).
    assert presence.classify(I(api_disabled=True, rsvp_notice=N.RSVP_HARD)) is S.UNKNOWN
    # If the online-merge actually shows them, that's confirmation.
    assert presence.classify(I(online=True, api_disabled=True)) is S.ONLINE_ELSEWHERE


def test_bar_flash_thresholds():
    # Here-and-gone flashes immediately, whatever the countdown says: they
    # have already shown they can make it.
    assert presence.view(I(was_online=True)).flash is True
    # OFFLINE escalates on the RSVP instead — the status no longer carries it.
    assert presence.view(I(rsvp_notice=N.RSVP_HARD, seconds_to_anni=600)).flash is True
    assert presence.view(I(rsvp_notice=N.RSVP_HARD, seconds_to_anni=3000)).flash is False
    assert presence.view(I(rsvp_notice=N.RSVP_SOFT, seconds_to_anni=2000)).flash is True
    assert presence.view(I(rsvp_notice=N.RSVP_SOFT, seconds_to_anni=3000)).flash is False
    # No RSVP at all => never nagged. They never said they were coming.
    assert presence.view(I(seconds_to_anni=60)).flash is False
    v = presence.view(I(online=True, has_party=False))
    assert v.status is S.ONLINE_ELSEWHERE and v.bar_class.startswith("bar-")


def test_offline_bar_message_still_names_the_rsvp_and_the_history():
    """The status collapsed to one value; the *copy* did not. A hard/soft
    RSVP who isn't on yet is still told which promise they're about to miss,
    and someone who was here and left gets the sharper line — that is the
    case the countdown can't soften."""
    assert "hard-RSVP" in presence.view(I(rsvp_notice=N.RSVP_HARD)).message
    assert "soft-RSVP" in presence.view(I(rsvp_notice=N.RSVP_SOFT)).message
    assert "not online" in presence.view(I()).message
    assert "We saw you around" in presence.view(I(was_online=True)).message
    # History wins over the RSVP wording — it is the more specific fact.
    assert "We saw you around" in presence.view(
        I(rsvp_notice=N.RSVP_HARD, was_online=True)).message
