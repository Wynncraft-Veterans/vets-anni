"""Authentication — intentionally LOW-TRUST (a coordination tool, not authn).

Per the spec + ``.claude/integration.md``: web login is IGN + *optional*
password. There is no email, no verification, no real identity proof — and
that is deliberate (minimum friction). Do **not** harden this into real auth;
the safety model is "no destructive action is exposed to anon/user sessions,
staff/admin actions sit behind the staff/admin password".

* **User:** resolve IGN -> UUID (``app.domain.identity``), upsert the
  :class:`AnniPlayer`, refresh tier/guild/last-online. First password set
  *sticks* (then required); none set + none given => straight in; staff can
  clear it.
* **Staff:** one shared password, hashed in :class:`AppConfig`
  (``staff_password_hash``), bootstrapped from ``STAFF_PASSWORD`` on first
  use, rotatable behind ``ADMIN_PASSWORD``.

Sessions are the signed cookie from ``app.web.deps`` — no session table.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from passlib.context import CryptContext

from app.db.models import AnniPlayer, AppConfig
from app.domain import identity, membership
from app.domain.identity import MojangResolver, mojang_username_to_uuid
from app.services.state import AppState
from app.settings import get_settings

logger = logging.getLogger("anni.auth")

#: pbkdf2_sha256 for user + staff passwords. Deliberately NOT bcrypt: passlib
#: 1.7.x's bcrypt backend is broken against bcrypt>=4 (its version self-test
#: throws), and bcrypt's 72-byte cap is a footgun. pbkdf2_sha256 is built into
#: passlib (zero native deps), unbounded input, and fits the 128-char column.
_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

_STAFF_PW_KEY = "staff_password_hash"


# --- AppConfig key/value helpers ------------------------------------------
async def get_config(key: str) -> object | None:
    row = await AppConfig.filter(key=key).first()
    return json.loads(row.value_json) if row else None


async def set_config(key: str, value: object) -> None:
    await AppConfig.update_or_create(
        key=key, defaults={"value_json": json.dumps(value)}
    )


# --- result type -----------------------------------------------------------
@dataclass(frozen=True)
class LoginOutcome:
    ok: bool
    player: AnniPlayer | None = None
    error: str | None = None
    needs_password: bool = False  # known user with a password, none/one wrong


# --- user login ------------------------------------------------------------
async def login_user(
    ign: str,
    password: str,
    state: AppState,
    *,
    mojang: MojangResolver = mojang_username_to_uuid,
) -> LoginOutcome:
    """Resolve + upsert the player and apply the optional-password rule.

    ``mojang`` is injectable so the flow is testable without network.
    """
    ign = (ign or "").strip()
    if not ign:
        return LoginOutcome(False, error="Enter your in-game name.")

    logger.debug("login: resolving IGN %r", ign)
    ident = await identity.resolve_identity(ign, state, mojang=mojang)
    if ident is None:
        logger.debug("login: IGN %r did not resolve to any UUID", ign)
        return LoginOutcome(
            False,
            error=f"Couldn't find a Minecraft account for “{ign}”. "
            "Check the spelling (it's your IGN, not your Discord name).",
        )

    settings = get_settings()
    tier = membership.resolve(
        in_returners_roster=ident.in_returners_roster,
        dazebot_tier=None,  # Phase 1 web path has no Discord link (fishbot=Phase 3)
        guild_name=ident.guild_name,
        guild_tag=ident.guild_tag,
        ally_tags=settings.ally_guild_tag_set,
        returners_guild_name=settings.returners_guild_name,
    )

    player, _created = await AnniPlayer.get_or_create(
        mc_uuid=ident.mc_uuid,
        defaults={
            "mc_username": ident.mc_username,
            "wynn_username": ident.wynn_username,
            "guild": ident.guild_name,
            "membership_tier": tier,
            "last_online": ident.last_online,
        },
    )
    # Refresh the cache fields every login (names/guild/tier drift over time).
    player.mc_username = ident.mc_username
    player.wynn_username = ident.wynn_username
    player.guild = ident.guild_name
    player.membership_tier = tier
    player.last_online = ident.last_online
    # A dashboard login is the canonical "this is a real user" signal —
    # clear the auto-promoter placeholder flag (one-way). The board's next
    # snapshot replaces their stub card with the real one.
    player.is_placeholder = False

    logger.debug(
        "login: %s -> uuid=%s tier=%s %s (pw %s)",
        ign, ident.mc_uuid, tier.value,
        "new" if _created else "returning",
        "set" if player.password_hash else "none",
    )

    # Optional-password rule. First set sticks; thereafter required.
    if player.password_hash:
        if not password or not _pwd.verify(password, player.password_hash):
            logger.debug("login: %s refused — password required/incorrect", ign)
            return LoginOutcome(
                False,
                needs_password=True,
                error="That account has a password set. Enter it (ask staff "
                "to reset it if you've forgotten).",
            )
    elif password:
        player.password_hash = _pwd.hash(password)
        logger.debug("login: %s set a password (now sticky)", ign)

    await player.save()
    logger.info("login ok: %s (%s, %s)", player.mc_username, ident.mc_uuid, tier.value)
    return LoginOutcome(True, player=player)


# --- staff login -----------------------------------------------------------
async def _staff_hash() -> str | None:
    """Current staff password hash, bootstrapping it from ``STAFF_PASSWORD``."""
    stored = await get_config(_STAFF_PW_KEY)
    if isinstance(stored, str) and stored:
        return stored
    boot = get_settings().staff_password
    if boot:
        h = _pwd.hash(boot)
        await set_config(_STAFF_PW_KEY, h)
        logger.info("staff password bootstrapped from STAFF_PASSWORD env")
        return h
    return None


async def check_staff_password(password: str) -> bool:
    h = await _staff_hash()
    if not h or not password:
        return False
    return _pwd.verify(password, h)


async def rotate_staff_password(admin_password: str, new_password: str) -> bool:
    """Rotate the staff password — gated by the (low-trust) admin password."""
    settings = get_settings()
    if not settings.admin_password or admin_password != settings.admin_password:
        return False
    if not new_password:
        return False
    await set_config(_STAFF_PW_KEY, _pwd.hash(new_password))
    logger.info("staff password rotated via admin gate")
    return True


async def clear_user_password(mc_uuid: str) -> bool:
    """Staff action: drop a user's password so they can log in freely again."""
    player = await AnniPlayer.filter(mc_uuid=mc_uuid).first()
    if not player:
        return False
    player.password_hash = None
    await player.save(update_fields=["password_hash"])
    return True


# --- request helpers -------------------------------------------------------
def _session(request: Request) -> dict:
    from app.web.deps import read_session

    return read_session(request)


async def current_user(request: Request) -> AnniPlayer | None:
    """The logged-in :class:`AnniPlayer`, or ``None``. Never raises."""
    sess = _session(request)
    if sess.get("kind") != "user" or not sess.get("mc_uuid"):
        return None
    return await AnniPlayer.filter(mc_uuid=sess["mc_uuid"]).first()


def is_staff(request: Request) -> bool:
    return _session(request).get("kind") == "staff"


def auth_redirect(request: Request, target: str = "/") -> Response:
    """Redirect for "no session, bounce away" — htmx-aware.

    A plain ``RedirectResponse`` is fine for full-page navigations, but the
    dashboard's 15 s ``hx-get="/me/specific"`` poll (and the other ``/me/*``
    fragments) follow the 303 transparently and swap the resulting login
    page's *innerHTML* into the polling div — producing a dashboard nested
    inside itself. Emit ``HX-Redirect`` for htmx callers so the browser does
    a real top-level navigation (which also kills the poll).
    """
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)
