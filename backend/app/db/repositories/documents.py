"""Document data access. EVERY function that takes a `user_id` filters on it (tenant isolation).

Status writes are conditional on the row still existing for that owner and return whether they
changed anything: a document deleted while it was rendered makes them False, which is the job's
signal to remove the file it just wrote. The one function without a user (`beyond_newest`) is the
retention sweep, which looks at every user and is only called by the periodic task.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document

__all__ = [
    "beyond_newest",
    "count_since",
    "create",
    "delete_ids",
    "for_messages",
    "get",
    "mark_failed",
    "mark_ready",
]


async def create(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    document_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    message_id: uuid.UUID | None,
    title: str,
    kind: str,
    draft: dict[str, object],
) -> Document:
    row = Document(
        id=document_id,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        title=title,
        kind=kind,
        draft=draft,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)  # server-side defaults and timestamps
    return row


async def get(session: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID) -> Document | None:
    stmt = select(Document).where(Document.user_id == user_id, Document.id == document_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_since(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> int:
    stmt = select(func.count()).where(Document.user_id == user_id, Document.created_at >= since)
    return int((await session.execute(stmt)).scalar_one())


async def for_messages(
    session: AsyncSession, user_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[Document]]:
    """The caller's documents by the assistant message that announced them (oldest first)."""
    if not message_ids:
        return {}
    stmt = (
        select(Document)
        .where(Document.user_id == user_id, Document.message_id.in_(message_ids))
        .order_by(Document.created_at, Document.id)
    )
    found: dict[uuid.UUID, list[Document]] = {}
    for row in (await session.execute(stmt)).scalars():
        if row.message_id is not None:
            found.setdefault(row.message_id, []).append(row)
    return found


async def mark_ready(
    session: AsyncSession,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    storage_path: str,
    page_count: int,
    size_bytes: int,
) -> bool:
    stmt = (
        update(Document)
        .where(Document.user_id == user_id, Document.id == document_id)
        .values(
            status="ready",
            storage_path=storage_path,
            page_count=page_count,
            size_bytes=size_bytes,
            error=None,
        )
    )
    return (await session.execute(stmt)).rowcount > 0  # type: ignore[attr-defined,no-any-return]


async def mark_failed(
    session: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID, reason: str
) -> bool:
    stmt = (
        update(Document)
        .where(Document.user_id == user_id, Document.id == document_id)
        .values(status="failed", error=reason)
    )
    return (await session.execute(stmt)).rowcount > 0  # type: ignore[attr-defined,no-any-return]


async def beyond_newest(
    session: AsyncSession, keep: int
) -> list[tuple[uuid.UUID, uuid.UUID, str | None]]:
    """(id, user id, storage path) of every document past each user's `keep` newest."""
    ranked = select(
        Document.id,
        Document.user_id,
        Document.storage_path,
        func.row_number()
        .over(
            partition_by=Document.user_id,
            order_by=(Document.created_at.desc(), Document.id.desc()),
        )
        .label("rank"),
    ).subquery()
    stmt = select(ranked.c.id, ranked.c.user_id, ranked.c.storage_path).where(ranked.c.rank > keep)
    return [(r[0], r[1], r[2]) for r in (await session.execute(stmt)).all()]


async def delete_ids(session: AsyncSession, ids: Sequence[uuid.UUID]) -> int:
    if not ids:
        return 0
    result = await session.execute(sql_delete(Document).where(Document.id.in_(ids)))
    return int(result.rowcount)  # type: ignore[attr-defined]
