import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RefreshToken


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> RefreshToken:
    token = RefreshToken(
        user_id=user_id, family_id=family_id, token_hash=token_hash, expires_at=expires_at
    )
    session.add(token)
    await session.flush()
    return token


async def get_by_hash_for_update(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    """Row-locked so concurrent uses of one token serialise (the second sees it revoked)."""
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def revoke(session: AsyncSession, token: RefreshToken, now: datetime) -> None:
    token.revoked_at = now
    await session.flush()


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def revoke_all_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    now: datetime,
    *,
    except_family: uuid.UUID | None = None,
) -> None:
    stmt = update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    if except_family is not None:
        stmt = stmt.where(RefreshToken.family_id != except_family)
    await session.execute(stmt.values(revoked_at=now))
