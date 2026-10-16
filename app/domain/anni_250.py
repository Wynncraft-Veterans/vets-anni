# >>> PATCH BEGIN: ANNI-250 (2026-10-16) — this whole file is the patch.
"""Anni #250 special-casing — ephemeral.

This whole patch will be removed in its entirety within the next few days;
don't build on it.

To celebrate the 250th, Anni #250 is run on level 30–40 classes and planned
through a form rather than fishbot RSVPs. So ``\\guess`` flags it with a
banner, ``\\rsvp hard|soft`` (and the in-game ``/wv anni rsvp``) turn people
to the form, and the grace-wipe keeps it off the record: no wins, losses or
missed RSVPs, whatever staff do on the board.

Remove: once Anni #250 has been wiped, delete this file and every
``PATCH BEGIN: ANNI-250`` … ``PATCH END: ANNI-250`` block
(``grep -rn "ANNI-250"``).
"""

from __future__ import annotations

from app.db.models import AnniEvent

#: The special anni.
NUMBER = 250
#: Where people indicate their plans for it, in place of an RSVP.
FORM_URL = (
    "https://1drv.ms/x/c/83fb5421eedb35c6/"
    "IQDxW_PnOrnPSp3UzfRYdXTLAaYhivsyBbQDrLnsNMYx46c?e=ZMAAco"
)


async def number_of(event: AnniEvent) -> int:
    """The anni number ``event`` carries, counted exactly as ``\\guess`` does.

    The anchor is ``\\guess``'s own rather than a copy, so a re-anchor there
    (as in 0bdf1a8) can't leave the two disagreeing about which anni is
    #250. Imported lazily because ``guess`` imports this module.
    """
    from app.bot.cogs.guess import _ANNI_ANCHOR_EPOCH, _ANNI_ANCHOR_NUMBER

    newer = await AnniEvent.filter(
        stamp_epoch__gt=_ANNI_ANCHOR_EPOCH,
        stamp_epoch__lte=event.stamp_epoch,
    ).count()
    return _ANNI_ANCHOR_NUMBER + newer


async def is_anni_250(event: AnniEvent | None) -> bool:
    """Whether ``event`` is Anni #250 (``None`` — no anni announced — is not)."""
    return event is not None and await number_of(event) == NUMBER
# <<< PATCH END: ANNI-250
