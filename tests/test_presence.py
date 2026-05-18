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


def test_offline_maps_by_rsvp():
    assert presence.classify(I(rsvp_notice=N.RSVP_HARD)) is S.OFFLINE_HARD
    assert presence.classify(I(rsvp_notice=N.RSVP_SOFT)) is S.OFFLINE_SOFT
    assert presence.classify(I()) is S.OFFLINE_GONE  # offline, no RSVP


def test_api_disabled_offline_is_unknown_but_online_merge_confirms():
    # Can't confirm -> UNKNOWN even with a hard RSVP (never faked online).
    assert presence.classify(I(api_disabled=True, rsvp_notice=N.RSVP_HARD)) is S.UNKNOWN
    # If the online-merge actually shows them, that's confirmation.
    assert presence.classify(I(online=True, api_disabled=True)) is S.ONLINE_ELSEWHERE


def test_bar_flash_thresholds():
    assert presence.view(I()).flash is True  # GONE flashes immediately
    assert presence.view(I(rsvp_notice=N.RSVP_HARD, seconds_to_anni=600)).flash is True
    assert presence.view(I(rsvp_notice=N.RSVP_HARD, seconds_to_anni=3000)).flash is False
    assert presence.view(I(rsvp_notice=N.RSVP_SOFT, seconds_to_anni=2000)).flash is True
    v = presence.view(I(online=True, has_party=False))
    assert v.status is S.ONLINE_ELSEWHERE and v.bar_class.startswith("bar-")
