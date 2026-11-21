"""Authentication use cases: register, login, refresh, logout, change password.

Transaction rule: a use case that must *persist something and then fail* (record a rate-limit hit,
revoke a token family after reuse) finishes its transaction first and raises afterwards. Raising
inside `session.begin()` would roll the write back.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import ratelimit
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import (
    REFRESH_TOKEN_TTL_S,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.db.models import User
from app.db.repositories import invites, refresh_tokens, users

Factory = async_sessionmaker[AsyncSession]

INVITE_INVALID = ApiError(403, "INVITE_INVALID", "This invite code is invalid, used or expired.")
_INVALID_CREDENTIALS = ("INVALID_CREDENTIALS", "Email or password is incorrect.")
_SESSION_INVALID = ("UNAUTHORIZED", "Your session is invalid or has expired.")


@dataclass(frozen=True)
class AuthResult:
    user: User
    access_token: str
    refresh_token: str


def _now() -> datetime:
    return datetime.now(UTC)


def _invite_invalid() -> ApiError:
    # A fresh exception per request (never re-raise a shared instance: tracebacks accumulate).
    return ApiError(INVITE_INVALID.status_code, INVITE_INVALID.code, INVITE_INVALID.message)


@lru_cache
def _dummy_hash() -> str:
    """Verified against when the email is unknown so both login paths cost the same."""
    return hash_password("nexus-dummy-password-for-timing")


async def _issue_session(
    session: AsyncSession, user: User, settings: Settings, *, family_id: uuid.UUID, now: datetime
) -> AuthResult:
    raw = generate_refresh_token()
    await refresh_tokens.create(
        session,
        user_id=user.id,
        family_id=family_id,
        token_hash=hash_token(raw),
        expires_at=now + timedelta(seconds=REFRESH_TOKEN_TTL_S),
    )
    return AuthResult(user, create_access_token(user.id, family_id, settings, now=now), raw)


# ───────────────────────────── register ─────────────────────────────


async def register(
    factory: Factory,
    settings: Settings,
    *,
    ip: str,
    email: str,
    password: str,
    display_name: str,
    invite_code: str | None,
) -> AuthResult:
    now = _now()
    invite_required = settings.registration_mode == "invite"
    invite_key = f"invite:{ip}"
    if invite_required:
        await ratelimit.check(factory, invite_key, ratelimit.INVITE_FAILURES_PER_WINDOW, now=now)

    password_hash = hash_password(password)  # CPU-heavy: before any row lock is taken

    result: AuthResult | None = None
    async with factory() as session, session.begin():
        invite = None
        if invite_required:
            # Validate the invite BEFORE touching users, so a caller without a valid invite can
            # never learn whether an email is registered (EMAIL_TAKEN is only reachable with one).
            invite = (
                await invites.lock_valid(session, hash_token(invite_code), now)
                if invite_code
                else None
            )
        if not invite_required or invite is not None:
            try:
                user = await users.create(
                    session, email=email, password_hash=password_hash, display_name=display_name
                )
            except users.EmailTakenError:
                # Raising rolls the transaction back, so the invite is NOT consumed.
                raise ApiError(
                    409, "EMAIL_TAKEN", "An account with this email already exists."
                ) from None
            if invite is not None:
                await invites.mark_used(session, invite, user.id, now)
            result = await _issue_session(session, user, settings, family_id=uuid.uuid4(), now=now)
    if result is None:
        await ratelimit.record(factory, invite_key, now=now)
        raise _invite_invalid()
    return result


# ───────────────────────────── login ─────────────────────────────


async def login(
    factory: Factory, settings: Settings, *, ip: str, email: str, password: str
) -> AuthResult:
    now = _now()
    key = f"login:{ip}:{hash_token(email.lower())}"
    await ratelimit.check(factory, key, ratelimit.LOGIN_FAILURES_PER_WINDOW, now=now)

    async with factory() as session:
        user = await users.get_by_email(session, email)
    # Always run one argon2 verification, so unknown emails aren't faster than wrong passwords.
    password_ok = verify_password(password, user.password_hash if user else _dummy_hash())

    if user is None or not password_ok or user.status == "pending_deletion":
        await ratelimit.record(factory, key, now=now)
        raise ApiError(401, *_INVALID_CREDENTIALS)
    if user.status == "disabled":  # only revealed to someone who proved the password
        raise ApiError(403, "ACCOUNT_DISABLED", "This account has been disabled.")

    async with factory() as session, session.begin():
        return await _issue_session(session, user, settings, family_id=uuid.uuid4(), now=now)


# ───────────────────────────── refresh ─────────────────────────────


async def refresh(factory: Factory, settings: Settings, *, token: str | None) -> AuthResult:
    """Rotate a refresh token. Reusing an already-rotated token revokes its whole family."""
    if not token:
        raise ApiError(401, *_SESSION_INVALID)
    now = _now()
    result: AuthResult | None = None

    async with factory() as session, session.begin():
        row = await refresh_tokens.get_by_hash_for_update(session, hash_token(token))
        if row is None or row.expires_at <= now:
            pass  # unknown or expired: nothing to revoke
        elif row.revoked_at is not None:
            # Token reuse: assume theft, kill every session descended from this login.
            await refresh_tokens.revoke_family(session, row.family_id, now)
        else:
            user = await users.get_by_id(session, row.user_id)
            if user is None or user.status != "active":
                await refresh_tokens.revoke_family(session, row.family_id, now)
            else:
                await refresh_tokens.revoke(session, row, now)
                result = await _issue_session(
                    session, user, settings, family_id=row.family_id, now=now
                )
    if result is None:
        raise ApiError(401, *_SESSION_INVALID)
    return result


# ───────────────────────────── logout / password ─────────────────────────────


async def logout(factory: Factory, *, user_id: uuid.UUID, token: str | None) -> None:
    """Revoke the session identified by the refresh cookie, if it belongs to the caller."""
    if not token:
        return
    now = _now()
    async with factory() as session, session.begin():
        row = await refresh_tokens.get_by_hash_for_update(session, hash_token(token))
        if row is not None and row.user_id == user_id:
            await refresh_tokens.revoke_family(session, row.family_id, now)


async def change_password(
    factory: Factory,
    *,
    user: User,
    family_id: uuid.UUID,
    current_password: str,
    new_password: str,
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise ApiError(400, "INVALID_PASSWORD", "Current password is incorrect.")
    new_hash = hash_password(new_password)
    now = _now()
    async with factory() as session, session.begin():
        await users.set_password_hash(session, user.id, new_hash)
        # Sign out every other device; the session that made the change stays.
        await refresh_tokens.revoke_all_for_user(session, user.id, now, except_family=family_id)
