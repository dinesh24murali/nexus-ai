"""`generate_title`: a title appears after the first exchange (TASK-012)."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import pytest
from app.core.config import Settings
from app.jobs import handlers, queue
from app.jobs.handlers import JobContext
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def exchange(
    db: psycopg.Connection[Any],
    user: str,
    *,
    title: str = "New chat",
    user_text: str = "What drove churn in Q3?",
    answer: str | None = "Mostly pricing changes.",
) -> str:
    row = db.execute(
        "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id", (user, title)
    ).fetchone()
    assert row
    cid = str(row[0])
    db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content)"
        " VALUES (%s, %s, 'user', %s)",
        (cid, user, user_text),
    )
    if answer is not None:
        db.execute(
            "INSERT INTO messages (conversation_id, user_id, role, content, created_at)"
            " VALUES (%s, %s, 'assistant', %s, now() + interval '1 second')",
            (cid, user, answer),
        )
    return cid


def title_of(db: psycopg.Connection[Any], cid: str) -> str:
    row = db.execute("SELECT title FROM conversations WHERE id = %s", (cid,)).fetchone()
    assert row
    return str(row[0])


def stub(text: str, **usage: Any) -> Runnable[Any, Any]:
    reply = AIMessage(
        content=text,
        usage_metadata=usage or None,
        response_metadata={"model_name": "gpt-6-luna"},
    )
    return GenericFakeChatModel(messages=iter([reply]))


def ctx(
    factory: Factory, settings: Settings, model: Runnable[Any, Any] | None = None
) -> JobContext:
    return JobContext(
        factory=factory, settings=settings, model_for=(lambda role: model) if model else None
    )


async def run(context: JobContext, user: str, cid: str, key: str | None = None) -> None:
    async with context.factory() as session, session.begin():
        await queue.enqueue(
            session,
            "generate_title",
            {"conversation_id": cid},
            key or f"title:{cid}",
            user_id=UUID(user),
        )
    job = await queue.claim(context.factory, "w1")
    assert job is not None
    await handlers.JOBS[job.kind].handler(context, job)


@pytest.fixture
def user(make_user: Any) -> Iterator[str]:
    yield make_user()


async def test_a_title_appears_after_the_first_exchange(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], user: str
) -> None:
    cid = exchange(db, user)

    await run(ctx(session_factory, settings), user, cid)  # LLM_MODE=fake title model

    assert title_of(db, cid) == "Fake title"


async def test_a_conversation_the_user_already_renamed_keeps_its_name(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], user: str
) -> None:
    cid = exchange(db, user, title="My own name")

    await run(ctx(session_factory, settings), user, cid)

    assert title_of(db, cid) == "My own name"


async def test_setting_the_title_does_not_count_as_activity(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], user: str
) -> None:
    cid = exchange(db, user)
    db.execute("UPDATE conversations SET updated_at = '2026-01-01T00:00:00Z' WHERE id = %s", (cid,))

    await run(ctx(session_factory, settings), user, cid)

    row = db.execute("SELECT updated_at FROM conversations WHERE id = %s", (cid,)).fetchone()
    assert row and row[0].year == 2026 and row[0].month == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Quarterly churn drivers"', "Quarterly churn drivers"),
        ("Title: Quarterly churn", "Quarterly churn"),
        ("  Quarterly\n churn.  ", "Quarterly churn"),
        ("Churn\x00 drivers", "Churn drivers"),
        ("x" * 300, "x" * 100),
    ],
)
async def test_the_model_output_is_cleaned_into_a_valid_title(
    raw: str,
    expected: str,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
) -> None:
    cid = exchange(db, user)

    await run(ctx(session_factory, settings, stub(raw)), user, cid)

    assert title_of(db, cid) == expected


async def test_an_empty_model_answer_leaves_the_default_title(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], user: str
) -> None:
    cid = exchange(db, user)

    await run(ctx(session_factory, settings, stub('  ""  ')), user, cid)

    assert title_of(db, cid) == "New chat"


async def test_nothing_happens_before_the_assistant_has_answered(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], user: str
) -> None:
    cid = exchange(db, user, answer=None)

    await run(ctx(session_factory, settings), user, cid)

    assert title_of(db, cid) == "New chat"


async def test_another_users_job_cannot_title_my_conversation(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], make_user: Any
) -> None:
    owner = make_user("owner@example.com")
    intruder = make_user("intruder@example.com")
    cid = exchange(db, owner)

    await run(ctx(session_factory, settings), intruder, cid)

    assert title_of(db, cid) == "New chat"


async def test_the_model_call_is_billed_to_the_user_without_counting_a_request(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], user: str
) -> None:
    cid = exchange(db, user)
    model = stub("Churn", input_tokens=1000, output_tokens=10, total_tokens=1010)

    await run(ctx(session_factory, settings, model), user, cid)

    row = db.execute(
        "SELECT prompt_tokens, completion_tokens, cost_usd, requests FROM usage_daily"
        " WHERE user_id = %s",
        (user,),
    ).fetchone()
    assert row == (1000, 10, Decimal("0.000105"), 0)  # 1000 x $0.10/M + 10 x $0.50/M


def test_every_job_kind_has_a_timeout_and_attempt_limit() -> None:
    spec = handlers.JOBS["generate_title"]
    assert spec.timeout_s == 120
    assert spec.max_attempts == 2  # "1 retry" in docs/02_outputs/08 section 9


async def test_the_title_job_is_traced_with_a_hashed_user(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], user: str
) -> None:
    class SpyTracer:
        def __init__(self) -> None:
            self.traces: list[Any] = []

        def run_config(self, trace: Any) -> dict[str, Any]:
            self.traces.append(trace)
            return {"callbacks": [], "run_name": trace.name, "tags": [], "metadata": {}}

    cid = exchange(db, user)
    tracer = SpyTracer()
    context = JobContext(
        factory=session_factory,
        settings=settings,
        tracer=tracer,  # type: ignore[arg-type]
    )

    await run(context, user, cid)

    (trace,) = tracer.traces
    assert trace.name == "job.generate_title"
    assert trace.conversation_id == cid
    assert user not in trace.user_hash
    assert title_of(db, cid) == "Fake title"
