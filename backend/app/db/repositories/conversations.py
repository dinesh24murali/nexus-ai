"""Conversation data access. EVERY function takes `user_id` and filters on it (tenant isolation)."""

import base64
import binascii
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation

DEFAULT_TITLE = "New chat"


class InvalidCursorError(ValueError):
    """The pagination cursor is not one we issued."""


def encode_cursor(updated_at: datetime, conversation_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{conversation_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True).decode()
        stamp, sep, ident = raw.partition("|")
        if not sep:
            raise ValueError("missing separator")
        updated_at = datetime.fromisoformat(stamp)
        if updated_at.tzinfo is None:
            raise ValueError("naive timestamp")
        return updated_at, uuid.UUID(ident)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid cursor") from exc


async def create(session: AsyncSession, user_id: uuid.UUID, title: str | None) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title or DEFAULT_TITLE)
    session.add(conversation)
    await session.flush()
    await session.refresh(conversation)  # server-side timestamps
    return conversation


async def get(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    stmt = select(Conversation).where(
        Conversation.user_id == user_id, Conversation.id == conversation_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_page(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None,
) -> tuple[list[Conversation], str | None]:
    """Most recently updated first. Keyset pagination on (updated_at DESC, id ASC), the exact order
    of `ix_conversations_user_updated`, so a page is one index range scan and activity between
    requests can never duplicate or skip rows."""
    stmt = select(Conversation).where(Conversation.user_id == user_id)
    if cursor is not None:
        updated_at, last_id = cursor
        stmt = stmt.where(
            or_(
                Conversation.updated_at < updated_at,
                and_(Conversation.updated_at == updated_at, Conversation.id > last_id),
            )
        )
    stmt = stmt.order_by(Conversation.updated_at.desc(), Conversation.id.asc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].updated_at, items[-1].id) if len(rows) > limit else None
    return items, next_cursor


async def rename(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, title: str
) -> Conversation | None:
    """Rename WITHOUT touching `updated_at`: that column means last activity (messages), and a
    rename must not float an old chat to the top of the sidebar."""
    stmt = (
        update(Conversation)
        .where(Conversation.user_id == user_id, Conversation.id == conversation_id)
        .values(title=title, updated_at=Conversation.updated_at)
        .returning(Conversation)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_generated_title(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, title: str
) -> bool:
    """Apply a generated title, but only while the conversation still has the default one, so a
    name the user chose (even while the job was queued) is never overwritten. Like `rename`, it
    leaves `updated_at` (last activity) alone."""
    stmt = (
        update(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.id == conversation_id,
            Conversation.title == DEFAULT_TITLE,
        )
        .values(title=title, updated_at=Conversation.updated_at)
        .returning(Conversation.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def acquire_lease(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, run_id: uuid.UUID
) -> bool:
    """Take the run lease if nobody holds it. One atomic UPDATE: of any number of concurrent
    sends exactly one matches `active_run_id IS NULL`."""
    stmt = (
        update(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.id == conversation_id,
            Conversation.active_run_id.is_(None),
        )
        .values(
            active_run_id=run_id, run_heartbeat_at=func.now(), updated_at=Conversation.updated_at
        )
        .returning(Conversation.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def heartbeat_lease(
    session: AsyncSession, conversation_id: uuid.UUID, run_id: uuid.UUID
) -> bool:
    """False when this run no longer holds the lease (the reaper took it)."""
    stmt = (
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.active_run_id == run_id)
        .values(run_heartbeat_at=func.now(), updated_at=Conversation.updated_at)
        .returning(Conversation.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def release_lease(
    session: AsyncSession, conversation_id: uuid.UUID, run_id: uuid.UUID
) -> bool:
    """Free the conversation and count the run as activity (`updated_at`, which orders the
    sidebar). False when the lease is not ours: the caller must then write nothing."""
    stmt = (
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.active_run_id == run_id)
        .values(active_run_id=None, run_heartbeat_at=None, updated_at=func.now())
        .returning(Conversation.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def stale_leases(
    session: AsyncSession, stale_after: timedelta
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """Conversations whose run stopped heart-beating: (conversation id, user id, run id), row-locked
    until the transaction ends. SKIP LOCKED: two reapers split the work instead of doubling it."""
    stmt = (
        select(Conversation.id, Conversation.user_id, Conversation.active_run_id)
        .where(
            Conversation.active_run_id.is_not(None),
            Conversation.run_heartbeat_at < func.now() - stale_after,
        )
        .with_for_update(skip_locked=True)
    )
    return [(r[0], r[1], r[2]) for r in (await session.execute(stmt)).all() if r[2] is not None]


async def clear_lease(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(active_run_id=None, run_heartbeat_at=None)
    )


# LangGraph keys its tables by thread_id (= conversation id) and exposes no user filter, so
# ownership is verified on `conversations` first.
_LANGGRAPH_THREAD_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


async def delete_with_checkpoints(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> bool:
    """Delete a conversation, its messages (FK cascade) and its LangGraph checkpoints."""
    deleted = await session.execute(
        delete(Conversation)
        .where(Conversation.user_id == user_id, Conversation.id == conversation_id)
        .returning(Conversation.id)
    )
    if deleted.scalar_one_or_none() is None:
        return False
    for table in _LANGGRAPH_THREAD_TABLES:  # fixed names, never user input
        await session.execute(
            text(f"DELETE FROM {table} WHERE thread_id = :tid"),  # noqa: S608
            {"tid": str(conversation_id)},
        )
    return True
