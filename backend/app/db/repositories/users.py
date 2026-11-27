"""User data access. Lookups by id/email are the pre-auth entry points."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class EmailTakenError(Exception):
    pass


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    # `email` is citext: the comparison is case-insensitive.
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def create(
    session: AsyncSession, *, email: str, password_hash: str, display_name: str
) -> User:
    user = User(email=email, password_hash=password_hash, display_name=display_name)
    try:
        # A savepoint, so a duplicate email doesn't poison the caller's transaction state.
        async with session.begin_nested():
            session.add(user)
    except IntegrityError as exc:
        raise EmailTakenError from exc
    return user


async def update_profile(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    display_name: str | None = None,
    memory_enabled: bool | None = None,
) -> User | None:
    values: dict[str, object] = {}
    if display_name is not None:
        values["display_name"] = display_name
    if memory_enabled is not None:
        values["memory_enabled"] = memory_enabled
    if values:
        await session.execute(update(User).where(User.id == user_id).values(**values))
    return await session.get(User, user_id, populate_existing=True)


async def set_password_hash(session: AsyncSession, user_id: uuid.UUID, password_hash: str) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(password_hash=password_hash)
    )


async def set_status(session: AsyncSession, user_id: uuid.UUID, status: str) -> None:
    await session.execute(update(User).where(User.id == user_id).values(status=status))
