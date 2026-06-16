"""Anni-timing prediction — earliest/median/latest window around an anchor.

Pulls the constants out of :mod:`app.bot.cogs.guess` so the snapshot path and
the ``\\guess`` Discord command share one source of truth. Pure: takes an
anchor epoch (the most recently confirmed anni stamp) and returns a dict
ready for JSON.

Model: ``Uniform(+71.4 h, +82.0 h)`` on the most recent confirmed event,
fitted from 472 days of data. Q0/Q4 are the support bounds; Q2 is the median.
"""

from __future__ import annotations

#: Quantile offsets in seconds. Mirror ``app.bot.cogs.guess``.
_Q0_OFFSET = 257_040  # +71.4 h  earliest possible
_Q2_OFFSET = 276_120  # +76.7 h  median
_Q4_OFFSET = 295_200  # +82.0 h  latest possible

#: Width of the support window in hours (Q0..Q4). Surfaced in the snapshot so
#: the vetsmod renderer can label the prediction line without re-deriving it.
WINDOW_HOURS = (_Q4_OFFSET - _Q0_OFFSET) / 3600  # ≈ 10.6


def predict_next(anchor_epoch: int) -> dict:
    """``{earliest_epoch, median_epoch, latest_epoch, window_hours}`` relative
    to ``anchor_epoch`` (the last confirmed anni stamp)."""
    return {
        "earliest_epoch": anchor_epoch + _Q0_OFFSET,
        "median_epoch": anchor_epoch + _Q2_OFFSET,
        "latest_epoch": anchor_epoch + _Q4_OFFSET,
        "window_hours": WINDOW_HOURS,
    }
