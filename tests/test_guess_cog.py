"""``\\guess`` — predicted-anni hybrid command.

Pure ``execute_guess`` against the ``db`` fixture with ``get_tempserver``
monkeypatched (same idiom as ``test_online_merge.py:72``). The image is a
shipped static asset — no Pillow in the runtime, just bytes-level checks.
"""

from __future__ import annotations

import time
from pathlib import Path


class _FakeTempserver:
    def __init__(self, value: int | None) -> None:
        self._value = value

    async def stamp(self) -> int | None:
        return self._value


async def test_guess_with_no_anchor(db, monkeypatch):
    from app.bot.cogs import guess as cog

    monkeypatch.setattr(cog, "get_tempserver", lambda: _FakeTempserver(None))
    reply = await cog.execute_guess()

    # No anchor → neither the prediction nor the confirmed branch fires.
    assert reply.text
    assert "Anni Predictions" not in reply.text
    assert "confirmed" not in reply.text
    assert reply.image_png is None


async def test_guess_with_past_stamp_predicts(db, monkeypatch):
    from app.bot.cogs import guess as cog

    past = int(time.time()) - 86_400  # one day ago
    monkeypatch.setattr(cog, "get_tempserver", lambda: _FakeTempserver(past))

    reply = await cog.execute_guess()

    assert "Anni Predictions for Anni #" in reply.text
    for label in ("Q₀", "Q₁", "Q₂", "Q₃", "Q₄"):
        assert label in reply.text
    # Five Discord timestamp tags — one per quartile, two forms each.
    assert reply.text.count("<t:") == 10
    # CLAUDE.md timezone rule — no wall-clock English.
    lowered = reply.text.lower()
    for banned in ("tonight", "today", "tomorrow", "this evening"):
        assert banned not in lowered, f"unexpected wall-clock word {banned!r}"
    # Image attached — first 8 bytes are the PNG signature.
    assert reply.image_png is not None
    assert reply.image_png[:8] == b"\x89PNG\r\n\x1a\n"


async def test_guess_with_future_stamp_shows_confirmed(db, monkeypatch):
    from app.bot.cogs import guess as cog

    future = int(time.time()) + 3_600  # one hour from now
    monkeypatch.setattr(cog, "get_tempserver", lambda: _FakeTempserver(future))

    reply = await cog.execute_guess()

    assert "confirmed" in reply.text
    assert f"<t:{future}:F>" in reply.text
    assert "Earliest Possible" not in reply.text  # box plot branch suppressed
    assert "Q₀" not in reply.text
    assert reply.image_png is None  # no plot when confirmed


def test_static_box_plot_asset_is_present():
    """The shipped PNG must exist and decode-signature-check at import-time
    (the cog reads it via ``Path.read_bytes()`` at module load, so a missing
    asset is a startup failure — guard against that regressing here)."""
    from app.bot.cogs import guess as cog

    assert cog._BOX_PLOT_PNG[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(cog._BOX_PLOT_PNG) > 1_000
    # And it lives at the expected path so deploys don't miss it.
    expected = (
        Path(cog.__file__).resolve().parent.parent
        / "resources"
        / "guess-box-plot.png"
    )
    assert expected.exists()
