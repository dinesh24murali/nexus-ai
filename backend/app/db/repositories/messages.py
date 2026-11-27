"""Message read model (the checkpointer is the source of truth). Owner-scoped like every repo."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.db.repositories.conversations import encode_cursor


def assistant_message_id(run_id: uuid.UUID) -> uuid.UUID:
    """The id promised to the client in `run_started`, derived from the run id so the reaper (a
    different process that only knows `active_run_id`) writes the very same message."""
    return uuid.uuid5(run_id, "assistant")


async def add(
    session: AsyncSession,
    *,
    id: uuid.UUID | None = None,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    content: str,
    status: str = "complete",
    finish_reason: str | None = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    cached_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: Decimal = Decimal(0),
    trace_id: str | None = None,
    tool_calls: list[Any] | None = None,
    citations: list[Any] | None = None,
    intent: str | None = None,
    route_source: str | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        content=content,
        status=status,
        finish_reason=finish_reason,
        model=model,
        prompt_tokens=prompt_tokens,
        cached_tokens=cached_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        trace_id=trace_id,
        tool_calls=tool_calls or [],
        citations=citations or [],
        intent=intent,
        route_source=route_source,
    )
    if id is not None:
        message.id = id
    session.add(message)
    await session.flush()
    return message


async def first_exchange(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> tuple[str, str] | None:
    """(first user message, first answer) of a conversation, or None until it has been answered."""
    stmt = (
        select(Message.role, Message.content)
        .where(Message.user_id == user_id, Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
        .limit(2)
    )
    rows = (await session.execute(stmt)).all()
    if len(rows) < 2 or [r.role for r in rows] != ["user", "assistant"]:
        return None
    return rows[0].content, rows[1].content


@dataclass(frozen=True)
class Turn:
    conversation_id: uuid.UUID
    user_text: str
    assistant_text: str


async def completed_turn(
    session: AsyncSession, user_id: uuid.UUID, assistant_message_id: uuid.UUID
) -> Turn | None:
    """The answer with this id and the user message it answers (the newest one before it), or
    None when the answer is not the caller's, not complete, or has no user message before it."""
    answer = (
        await session.execute(
            select(Message).where(
                Message.id == assistant_message_id,
                Message.user_id == user_id,
                Message.role == "assistant",
                Message.status == "complete",
            )
        )
    ).scalar_one_or_none()
    if answer is None:
        return None
    asked = (
        await session.execute(
            select(Message.content)
            .where(
                Message.conversation_id == answer.conversation_id,
                Message.user_id == user_id,
                Message.role == "user",
                Message.created_at <= answer.created_at,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if asked is None:
        return None
    return Turn(answer.conversation_id, asked, answer.content)


async def list_page(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None,
) -> tuple[list[Message], str | None]:
    """Newest first, keyset on (created_at DESC, id DESC): the order of
    `ix_messages_conversation_created` read backwards, so messages that arrive between two
    requests can neither duplicate nor skip a row."""
    stmt = select(Message).where(
        Message.user_id == user_id, Message.conversation_id == conversation_id
    )
    if cursor is not None:
        created_at, last_id = cursor
        stmt = stmt.where(
            or_(
                Message.created_at < created_at,
                and_(Message.created_at == created_at, Message.id < last_id),
            )
        )
    stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
    return items, next_cursor


async def latest(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Message | None:
    stmt = (
        select(Message)
        .where(Message.user_id == user_id, Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_assistant(
    session: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    """One of the user's own assistant messages (the only kind that can be rated)."""
    stmt = select(Message).where(
        Message.user_id == user_id, Message.id == message_id, Message.role == "assistant"
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def delete_message(session: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID) -> bool:
    result = await session.execute(
        delete(Message).where(Message.user_id == user_id, Message.id == message_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def last_chat_answer(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Message | None:
    """The newest answer worth turning into a document: complete, with text, and written by chat
    or research. A memory answer, a document announcement and a static reply (`/help`, a usage
    message: no intent, `route_source` `command`) are skipped."""
    stmt = (
        select(Message)
        .where(
            Message.user_id == user_id,
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.status == "complete",
            Message.content != "",
            or_(
                Message.intent.in_(("general_chat", "deep_research")),
                and_(Message.intent.is_(None), Message.route_source.is_(None)),
            ),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def recent_texts(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
) -> list[tuple[str, str]]:
    """(role, text) of the newest `limit` messages with text, oldest first. The newest message is
    the one that asked for the document, so it is left out when it is the user's."""
    stmt = (
        select(Message.role, Message.content)
        .where(
            Message.user_id == user_id,
            Message.conversation_id == conversation_id,
            Message.content != "",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit + 1)
    )
    rows = [(r.role, r.content) for r in (await session.execute(stmt)).all()]
    if rows and rows[0][0] == "user":
        rows = rows[1:]
    return list(reversed(rows[:limit]))
