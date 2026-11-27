"""Rating an answer: the rating is stored first, then copied to the answer's Langfuse trace as the
score `user_feedback` (docs/02_outputs/09 section 7: dashboards read it by feature and prompt
version). The database is the record; Langfuse is a best-effort mirror, so a tracing outage never
fails the request and an answer that has no trace (written before tracing was on, or by the
reaper) is rated all the same."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError
from app.db.models import Feedback
from app.db.repositories import feedback as feedback_repo
from app.db.repositories import messages
from app.llm.tracing import Tracer


class FeedbackService:
    def __init__(self, factory: async_sessionmaker[AsyncSession], tracer: Tracer | None) -> None:
        self._factory = factory
        self._tracer = tracer

    async def rate(
        self, user_id: uuid.UUID, message_id: uuid.UUID, *, score: int, comment: str | None
    ) -> Feedback:
        async with self._factory() as session, session.begin():
            message = await messages.get_assistant(session, user_id, message_id)
            if message is None:
                # Someone else's message, a user message and an unknown id look the same.
                raise ApiError(404, "NOT_FOUND", "Message not found.")
            row = await feedback_repo.upsert(
                session, user_id, message_id, score=score, comment=comment
            )
            trace_id = message.trace_id
        if self._tracer is not None and trace_id:
            self._tracer.score_feedback(
                trace_id, message_id=str(message_id), score=score, comment=comment
            )
        return row
