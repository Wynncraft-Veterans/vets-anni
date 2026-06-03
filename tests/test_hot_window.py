"""hot_window — pure-function predicates for the cadence ramp + LATE switch.

Two consumers must agree exactly:

* the auto-promoter (`is_hot` to decide whether to scan,
  `is_late_bucket` to choose the dropzone);
* `board_view.snapshot` (`monitoring_state` -> the live-pill label).

Tests use injected ``now`` so they're deterministic; no wall-clock reliance.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services import hot_window


@dataclass
class _Ev:
    """Stamped-event stub. The hot_window helpers only read ``stamp_epoch``."""
    stamp_epoch: int


HOT_OPEN = 70 * 60   # 1h10
GRACE = 2 * 3600     # 2h


def test_is_hot_outside_window_returns_false():
    e = _Ev(stamp_epoch=10_000)
    # T-2h (well before the open boundary).
    assert not hot_window.is_hot(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 - 2 * 3600,
    )


def test_is_hot_inside_window_returns_true():
    e = _Ev(stamp_epoch=10_000)
    # T-65min (inside the 70min window).
    assert hot_window.is_hot(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 - 65 * 60,
    )
    # T-1min (still pending, still hot).
    assert hot_window.is_hot(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 - 60,
    )


def test_is_hot_during_grace_returns_true():
    e = _Ev(stamp_epoch=10_000)
    # Stamp + 1h (inside the 2h grace).
    assert hot_window.is_hot(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 + 3600,
    )


def test_is_hot_after_grace_returns_false():
    e = _Ev(stamp_epoch=10_000)
    # Stamp + 3h (past 2h grace = EXPIRED).
    assert not hot_window.is_hot(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 + 3 * 3600,
    )


def test_is_hot_no_event_returns_false():
    assert not hot_window.is_hot(
        None, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE, now=0,
    )


def test_is_late_bucket_before_cutoff_returns_false():
    e = _Ev(stamp_epoch=10_000)
    # T-65min (above the 60min EARLY cutoff).
    assert not hot_window.is_late_bucket(e, now=10_000 - 65 * 60)


def test_is_late_bucket_at_or_after_cutoff_returns_true():
    e = _Ev(stamp_epoch=10_000)
    # T-59min (just under cutoff).
    assert hot_window.is_late_bucket(e, now=10_000 - 59 * 60)
    # During grace (negative seconds_to_anni).
    assert hot_window.is_late_bucket(e, now=10_000 + 1800)


def test_is_late_bucket_no_event_returns_false():
    assert not hot_window.is_late_bucket(None, now=0)


def test_is_rsvp_closed_before_t90_returns_false():
    e = _Ev(stamp_epoch=10_000)
    # T-91min (just outside the 90min cutoff — RSVPs still accepted).
    assert not hot_window.is_rsvp_closed(e, now=10_000 - 91 * 60)
    # T-3h (well before the cutoff).
    assert not hot_window.is_rsvp_closed(e, now=10_000 - 3 * 3600)


def test_is_rsvp_closed_at_or_after_t90_returns_true():
    e = _Ev(stamp_epoch=10_000)
    # T-89min (just inside the cutoff).
    assert hot_window.is_rsvp_closed(e, now=10_000 - 89 * 60)
    # T-30min (well inside).
    assert hot_window.is_rsvp_closed(e, now=10_000 - 30 * 60)
    # During grace and after expiry: still closed (seconds_to_anni < 0).
    assert hot_window.is_rsvp_closed(e, now=10_000 + 1800)
    assert hot_window.is_rsvp_closed(e, now=10_000 + 10 * 3600)


def test_is_rsvp_closed_no_event_returns_false():
    # No event => "open" so the cog's no-event branch produces the friendlier
    # "no anni announced" message instead of "RSVPs are closed".
    assert not hot_window.is_rsvp_closed(None, now=0)


def test_monitoring_state_idle_outside_window():
    e = _Ev(stamp_epoch=10_000)
    assert hot_window.monitoring_state(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 - 2 * 3600,
    ) == "idle"
    assert hot_window.monitoring_state(
        None, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE, now=0,
    ) == "idle"


def test_monitoring_state_early_between_70_and_60():
    e = _Ev(stamp_epoch=10_000)
    assert hot_window.monitoring_state(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 - 65 * 60,
    ) == "early"


def test_monitoring_state_late_inside_60_and_during_grace():
    e = _Ev(stamp_epoch=10_000)
    assert hot_window.monitoring_state(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 - 30 * 60,
    ) == "late"
    assert hot_window.monitoring_state(
        e, hot_window_open_seconds=HOT_OPEN, grace_seconds=GRACE,
        now=10_000 + 3600,  # grace
    ) == "late"


def test_monitoring_labels_cover_every_state():
    # No accidental KeyError in the live-pill render path.
    for state in ("idle", "early", "late"):
        assert hot_window.MONITORING_LABEL[state].startswith("Live")


def test_currently_hot_cache_roundtrips():
    # Sync cache for the online_merge / presence_poller interval pickers.
    hot_window.set_currently_hot(False)
    assert hot_window.is_currently_hot() is False
    hot_window.set_currently_hot(True)
    assert hot_window.is_currently_hot() is True
    hot_window.set_currently_hot(False)  # reset so other tests aren't sticky
