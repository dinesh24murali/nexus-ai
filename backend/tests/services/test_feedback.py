"""FeedbackService: the rating is stored, then mirrored to the answer's trace (best effort)."""

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from app.core.errors import ApiError
from app.services.feedback import FeedbackService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


class ScoringTracer:
    def __init__(self, *, fail: bool = False) -> None:
        self.scores: list[dict[str, Any]] = []
        self._fail = fail

    def score_feedback(
        self, trace_id: str, *, message_id: str, score: int, comment: str | None
    ) -> bool:
        self.scores.append(
            {"trace_id": trace_id, "message_id": message_id, "score": score, "comment": comment}
        )
        return not self._fail


def seed(db: psycopg.Connection[Any], *, trace_id: str | None) -> tuple[UUID, UUID]:
    owner = db.execute(
        "INSERT INTO users (email, password_hash, display_name) VALUES (%s, 'x', 'A') RETURNING id",
        (f"{uuid4()}@example.com",),
    ).fetchone()
    assert owner
    conv = db.execute(
        "INSERT INTO conversations (user_id, title) VALUES (%s, 't') RETURNING id", (owner[0],)
    ).fetchone()
    assert conv
    msg = db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content, trace_id)"
        " VALUES (%s, %s, 'assistant', 'hi', %s) RETURNING id",
        (conv[0], owner[0], trace_id),
    ).fetchone()
    assert msg
    return owner[0], msg[0]


async def test_the_rating_is_scored_on_the_trace_of_the_answer(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    tracer = ScoringTracer()
    owner, message = seed(db, trace_id="a" * 32)

    row = await FeedbackService(session_factory, tracer).rate(  # type: ignore[arg-type]
        owner, message, score=-1, comment="wrong"
    )

    assert (row.score, row.comment) == (-1, "wrong")
    assert tracer.scores == [
        {"trace_id": "a" * 32, "message_id": str(message), "score": -1, "comment": "wrong"}
    ]


async def test_an_answer_without_a_trace_is_rated_but_nothing_is_sent(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    tracer = ScoringTracer()
    owner, message = seed(db, trace_id=None)

    row = await FeedbackService(session_factory, tracer).rate(  # type: ignore[arg-type]
        owner, message, score=1, comment=None
    )

    assert row.score == 1 and tracer.scores == []


async def test_a_service_without_a_tracer_still_stores_the_rating(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    owner, message = seed(db, trace_id="b" * 32)
    row = await FeedbackService(session_factory, None).rate(owner, message, score=1, comment=None)
    assert row.score == 1


async def test_a_failing_score_does_not_undo_the_rating(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    owner, message = seed(db, trace_id="c" * 32)

    await FeedbackService(session_factory, ScoringTracer(fail=True)).rate(  # type: ignore[arg-type]
        owner, message, score=1, comment=None
    )

    stored = db.execute("SELECT score FROM feedback WHERE message_id = %s", (message,))
    assert stored.fetchall() == [(1,)]


async def test_another_users_message_is_not_scored_and_not_stored(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    tracer = ScoringTracer()
    _, message = seed(db, trace_id="d" * 32)
    stranger, _ = seed(db, trace_id=None)

    with pytest.raises(ApiError) as caught:
        await FeedbackService(session_factory, tracer).rate(  # type: ignore[arg-type]
            stranger, message, score=-1, comment="spam"
        )

    assert caught.value.status_code == 404
    assert tracer.scores == []  # nothing reaches the other user's trace
    assert db.execute("SELECT count(*) FROM feedback").fetchone() == (0,)
