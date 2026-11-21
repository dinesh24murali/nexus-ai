"""A user's thumbs up/down on an assistant message. Owner-scoped like every repository: the
`user_id` is always part of the key, so one user's rating can neither be read nor overwritten by
another."""

import uuid
from collections.abc import Collection

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback


async def upsert(
    session: AsyncSession,
    user_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    score: int,
    comment: str | None,
) -> Feedback:
    """Save the rating, replacing the user's earlier one for this message (PUT semantics: a
    rating without a comment clears the old comment). `created_at` becomes the time of the
    latest rating."""
    stmt = insert(Feedback).values(
        user_id=user_id, message_id=message_id, score=score, comment=comment
    )
    upsert_stmt = stmt.on_conflict_do_update(
        constraint="uq_feedback_message_user",
        set_={
            "score": stmt.excluded.score,
            "comment": stmt.excluded.comment,
            "created_at": func.now(),
        },
    ).returning(Feedback)
    return (await session.scalars(upsert_stmt, execution_options={"populate_existing": True})).one()


async def scores_for(
    session: AsyncSession, user_id: uuid.UUID, message_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """The user's rating for each of `message_ids` that has one."""
    if not message_ids:
        return {}
    stmt = select(Feedback.message_id, Feedback.score).where(
        Feedback.user_id == user_id, Feedback.message_id.in_(message_ids)
    )
    return {row.message_id: row.score for row in (await session.execute(stmt)).all()}
