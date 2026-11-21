"""File data access. EVERY function takes `user_id` and filters on it (tenant isolation).

Status writes are conditional on the row still existing for that owner and return whether they
changed anything: a file deleted while it was processed makes them False, which is the worker's
signal to clean up and stop.
"""

import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import File, User
from app.db.repositories.conversations import (  # cursors are (timestamp, id) keysets
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)

__all__ = [
    "InvalidCursorError",
    "count",
    "create",
    "decode_cursor",
    "get",
    "get_by_sha",
    "list_page",
    "lock_user",
    "mark_failed",
    "mark_processing",
    "mark_ready",
    "recent_ready",
]


async def lock_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Serialise uploads of one user (the per-user file limit is check-then-insert)."""
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


async def create(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    file_id: uuid.UUID,
    filename: str,
    mime_type: str,
    size_bytes: int,
    sha256: str,
    storage_path: str,
) -> File:
    row = File(
        id=file_id,
        user_id=user_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_path=storage_path,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)  # server-side defaults and timestamps
    return row


async def get(session: AsyncSession, user_id: uuid.UUID, file_id: uuid.UUID) -> File | None:
    stmt = select(File).where(File.user_id == user_id, File.id == file_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_sha(session: AsyncSession, user_id: uuid.UUID, sha256: str) -> File | None:
    stmt = select(File).where(File.user_id == user_id, File.sha256 == sha256)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(File).where(File.user_id == user_id)
    return int((await session.execute(stmt)).scalar_one())


async def list_page(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None,
) -> tuple[list[File], str | None]:
    """Newest first. Keyset pagination on (created_at DESC, id ASC), as `ix_files_user_created`."""
    stmt = select(File).where(File.user_id == user_id)
    if cursor is not None:
        created_at, last_id = cursor
        stmt = stmt.where(
            or_(
                File.created_at < created_at,
                and_(File.created_at == created_at, File.id > last_id),
            )
        )
    stmt = stmt.order_by(File.created_at.desc(), File.id.asc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
    return items, next_cursor


async def recent_ready(session: AsyncSession, user_id: uuid.UUID, *, limit: int) -> list[File]:
    """The caller's newest files that can be searched (status `ready`)."""
    stmt = (
        select(File)
        .where(File.user_id == user_id, File.status == "ready")
        .order_by(File.created_at.desc(), File.id.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def _set(
    session: AsyncSession, user_id: uuid.UUID, file_id: uuid.UUID, **values: object
) -> bool:
    stmt = update(File).where(File.user_id == user_id, File.id == file_id).values(**values)
    return bool((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


async def mark_processing(session: AsyncSession, user_id: uuid.UUID, file_id: uuid.UUID) -> bool:
    return await _set(session, user_id, file_id, status="processing", error=None)


async def mark_ready(
    session: AsyncSession,
    user_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    chunk_count: int,
    page_count: int | None,
) -> bool:
    return await _set(
        session,
        user_id,
        file_id,
        status="ready",
        error=None,
        chunk_count=chunk_count,
        page_count=page_count,
    )


async def mark_failed(
    session: AsyncSession, user_id: uuid.UUID, file_id: uuid.UUID, error: str
) -> bool:
    return await _set(session, user_id, file_id, status="failed", error=error)
