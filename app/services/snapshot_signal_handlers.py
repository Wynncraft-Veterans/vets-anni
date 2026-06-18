"""Tortoise ``post_save`` handlers that drive the snapshot delta push.

Every model whose fields are consumed by :func:`app.domain.snapshot.assemble_snapshot`
gets a handler here. The handler resolves the affected player UUID(s) and
delegates to :func:`app.services.snapshot_notifier.get().notify_uuids` (or
``notify_all`` for mass-fanout). Notifications are fire-and-forget HTTP
POSTs to temp-server's ``/api/internal/anni-snapshot-delta`` — temp-server
then refetches and pushes to vetsmod sessions whose subscription set
includes the named UUIDs.

**No tier gating happens here.** vets-anni doesn't track which sessions
have actually subscribed (that state lives on temp-server, populated at
WS-auth time via the tier check OR ``/api/internal/anni-exists/{uuid}``
lookup). So we send notifications for every save and let temp-server's
subscription set act as the filter. Cost is one HTTP POST per save that
temp-server drops at the door — negligible at current write volumes,
and correct for community/other-tier users who DID subscribe via the
existence check.

Mass-fanout vectors (grace-wipe, bulk queryset updates) bypass
``post_save`` entirely; they have explicit ``notify_all`` calls at the
call site instead of relying on this module — see
:func:`app.services.lifecycle_task._wipe` for the canonical example.

Registration is import-side-effect: importing this module wires the
decorators into Tortoise's signal registry. Import happens from
:func:`app.db.lifecycle.init` after ``Tortoise.init`` so the registration
order is deterministic.
"""

from __future__ import annotations

import logging
from typing import Any

from tortoise.signals import post_save

from app.db.models import (
    AnniEvent,
    AnniPlayer,
    BoardPlacement,
    Party,
    RoleCapability,
    Rsvp,
)
from app.services import snapshot_notifier

logger = logging.getLogger("anni.snapshot_signals")


def _notify_uuids(uuids: list[str], *, label: str) -> None:
    """Wrap the notifier call with a try/except so a signal handler can
    never propagate an exception back into the save path. The DB write
    has already committed by the time we get here — losing the delta
    push is recoverable (the 10 s polling safety net catches it) but a
    crash here would surface as an unexpected error to the caller."""
    try:
        snapshot_notifier.get().notify_uuids(uuids)
    except Exception:
        logger.exception("signal_handlers[%s]: notify_uuids failed", label)


def _notify_all(*, label: str) -> None:
    try:
        snapshot_notifier.get().notify_all()
    except Exception:
        logger.exception("signal_handlers[%s]: notify_all failed", label)


# ----- single-uuid handlers (FK ``player_id`` is the mc_uuid PK) ------------
# Tortoise exposes the FK column value directly as ``instance.<name>_id``
# without needing fetch_related; AnniPlayer.mc_uuid IS the primary key, so
# the FK column carries the uuid string verbatim.


@post_save(AnniPlayer)
async def _on_anni_player_saved(
    sender: type[AnniPlayer],
    instance: AnniPlayer,
    created: bool,
    using_db: Any,
    update_fields: Any,
) -> None:
    _notify_uuids([instance.mc_uuid], label="AnniPlayer")


@post_save(Rsvp)
async def _on_rsvp_saved(
    sender: type[Rsvp],
    instance: Rsvp,
    created: bool,
    using_db: Any,
    update_fields: Any,
) -> None:
    uuid = getattr(instance, "player_id", None)
    if uuid:
        _notify_uuids([str(uuid)], label="Rsvp")


@post_save(BoardPlacement)
async def _on_board_placement_saved(
    sender: type[BoardPlacement],
    instance: BoardPlacement,
    created: bool,
    using_db: Any,
    update_fields: Any,
) -> None:
    uuid = getattr(instance, "player_id", None)
    if uuid:
        _notify_uuids([str(uuid)], label="BoardPlacement")


@post_save(RoleCapability)
async def _on_role_capability_saved(
    sender: type[RoleCapability],
    instance: RoleCapability,
    created: bool,
    using_db: Any,
    update_fields: Any,
) -> None:
    uuid = getattr(instance, "player_id", None)
    if uuid:
        _notify_uuids([str(uuid)], label="RoleCapability")


# ----- fan-out handlers ----------------------------------------------------


@post_save(Party)
async def _on_party_saved(
    sender: type[Party],
    instance: Party,
    created: bool,
    using_db: Any,
    update_fields: Any,
) -> None:
    """Party world/host/result/scroll_spot changes affect every member's
    snapshot — fan out to all current placements pointing at this party."""
    try:
        uuids = await (
            BoardPlacement.filter(party_id=instance.id, event_id=instance.event_id)
            .values_list("player_id", flat=True)
        )
    except Exception:
        logger.exception("signal_handlers[Party]: roster fanout query failed")
        return
    if uuids:
        _notify_uuids([str(u) for u in uuids], label="Party")


@post_save(AnniEvent)
async def _on_anni_event_saved(
    sender: type[AnniEvent],
    instance: AnniEvent,
    created: bool,
    using_db: Any,
    update_fields: Any,
) -> None:
    """Any AnniEvent save (create, activation toggle, grace-open, wipe stamp)
    can shift the snapshot for every subscribed user — predictions, event
    block, all-parties listing. Tortoise post_save doesn't reliably tell us
    which fields changed, so we conservatively fire ``notify_all`` for every
    AnniEvent write. Frequency is low (a handful per day at most); the
    blanket refresh costs one batch fetch on temp-server."""
    _notify_all(label="AnniEvent")
