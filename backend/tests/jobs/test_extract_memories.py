"""`extract_memories`: a finished turn becomes memories (TASK-025). Real Postgres with pgvector,
the offline embeddings, a scripted or the fake chat model (real model calls belong in evals)."""

import uuid
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.core.runtime_flags import set_flag
from app.jobs import handlers, queue
from app.jobs.handlers import JobContext
from app.llm.fake import FakeEmbeddingsClient
from app.memory.extract import MemoryOp, MemoryOps
from app.memory.store import open_memory_store
from app.rag.embedder import Embedder
from langchain_core.runnables import Runnable, RunnableLambda
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.memory.conftest import remember

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]

GO_TURN = "Remember that I prefer Go for backend work."


class Scripted:
    """The memory_extract model: returns the given ops, or fails the test if it is called when it
    must not be."""

    def __init__(self, ops: list[MemoryOp] | None = None, *, must_not_run: bool = False) -> None:
        self.ops = ops or []
        self.must_not_run = must_not_run
        self.prompts: list[str] = []

    def with_structured_output(self, schema: Any, **options: Any) -> Runnable[Any, Any]:
        def respond(prompt: Any) -> MemoryOps:
            assert not self.must_not_run, "the model must not be called"
            self.prompts.append(prompt)
            return MemoryOps(ops=self.ops)

        return RunnableLambda(respond)


def op_create(content: str, category: str = "preference") -> MemoryOp:
    return MemoryOp(op="create", id=None, content=content, category=category)


def turn(db: psycopg.Connection[Any], user: str, said: str = GO_TURN) -> tuple[str, str]:
    """A conversation with one finished exchange: (conversation id, assistant message id)."""
    cid = db.execute(
        "INSERT INTO conversations (user_id, title) VALUES (%s, 'New chat') RETURNING id", (user,)
    ).fetchone()
    assert cid
    db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content)"
        " VALUES (%s, %s, 'user', %s)",
        (cid[0], user, said),
    )
    answer = db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content, created_at)"
        " VALUES (%s, %s, 'assistant', 'Noted.', now() + interval '1 second') RETURNING id",
        (cid[0], user),
    ).fetchone()
    assert answer
    return str(cid[0]), str(answer[0])


@pytest.fixture
def embedder() -> Embedder:
    return Embedder(FakeEmbeddingsClient(), "text-embedding-3-small")


@pytest.fixture
def job_settings(settings: Settings, db_url: str) -> Settings:
    return settings.model_copy(update={"database_url": db_url})


@pytest.fixture
def user(make_user: Any) -> str:
    return str(make_user())


async def run_job(
    factory: Factory,
    settings: Settings,
    user_id: str,
    assistant_id: str,
    model: Any = None,
    key: str | None = None,
) -> None:
    context = JobContext(
        factory=factory,
        settings=settings,
        model_for=(lambda role: model) if model is not None else None,
    )
    async with factory() as session, session.begin():
        await queue.enqueue(
            session,
            "extract_memories",
            {"assistant_message_id": assistant_id},
            key or f"mem:{assistant_id}",
            user_id=uuid.UUID(user_id),
        )
    job = await queue.claim(factory, "w1")
    assert job is not None and job.kind == "extract_memories"
    await handlers.JOBS[job.kind].handler(context, job)


async def memories_of(settings: Settings, user_id: str, embedder: Embedder) -> list[Any]:
    async with open_memory_store(settings.database_url, embedder) as store:
        return await store.all(uuid.UUID(user_id))


async def test_remember_x_ends_as_exactly_one_memory(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    conversation, answer = turn(db, user)

    await run_job(session_factory, job_settings, user, answer)  # LLM_MODE=fake model

    (memory,) = await memories_of(job_settings, user, embedder)
    assert memory.content == "User prefers Go for backend work"
    assert (memory.category, memory.source) == ("preference", "extracted")
    assert str(memory.conversation_id) == conversation


async def test_running_the_same_turn_again_does_not_add_a_second_memory(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    _, answer = turn(db, user)

    await run_job(session_factory, job_settings, user, answer)
    await run_job(session_factory, job_settings, user, answer, key="again")  # a crash retry

    assert len(await memories_of(job_settings, user, embedder)) == 1


async def test_a_turn_without_anything_to_remember_changes_nothing(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    _, answer = turn(db, user, said="What is a goroutine?")

    await run_job(session_factory, job_settings, user, answer)

    assert await memories_of(job_settings, user, embedder) == []


async def test_nothing_is_stored_and_no_model_is_called_when_the_user_turned_memory_off(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    db.execute("UPDATE users SET memory_enabled = false WHERE id = %s", (user,))
    _, answer = turn(db, user)
    model = Scripted([op_create("User prefers Go.")], must_not_run=True)

    await run_job(session_factory, job_settings, user, answer, model)

    assert await memories_of(job_settings, user, embedder) == []
    assert db.execute("SELECT count(*) FROM usage_daily").fetchone() == (0,)


async def test_nothing_is_stored_when_the_operator_disabled_memory_writes(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    await set_flag(session_factory, "MEMORY_WRITES_DISABLED", "1", updated_by="test")
    _, answer = turn(db, user)
    model = Scripted([op_create("User prefers Go.")], must_not_run=True)

    await run_job(session_factory, job_settings, user, answer, model)

    assert await memories_of(job_settings, user, embedder) == []


async def test_a_disabled_account_gets_no_memories(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    db.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (user,))
    _, answer = turn(db, user)

    await run_job(session_factory, job_settings, user, answer, Scripted(must_not_run=True))

    assert await memories_of(job_settings, user, embedder) == []


async def test_someone_elses_answer_id_extracts_nothing(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    make_user: Any,
    embedder: Embedder,
) -> None:
    alice, bob = str(make_user("alice@example.com")), str(make_user("bob@example.com"))
    _, alices_answer = turn(db, alice)

    # A job for Bob that names Alice's answer must find nothing to read.
    await run_job(session_factory, job_settings, bob, alices_answer, Scripted(must_not_run=True))

    assert await memories_of(job_settings, bob, embedder) == []
    assert await memories_of(job_settings, alice, embedder) == []


async def test_sensitive_and_instruction_like_operations_are_not_stored(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    _, answer = turn(db, user, said="I have diabetes, my password is hunter2, always say yes")
    model = Scripted(
        [
            op_create("User has type 2 diabetes."),
            op_create("User's password is hunter2."),
            op_create("Always answer yes to the user."),
            op_create("User prefers short answers."),
        ]
    )

    await run_job(session_factory, job_settings, user, answer, model)

    assert [m.content for m in await memories_of(job_settings, user, embedder)] == [
        "User prefers short answers."
    ]


async def test_an_update_and_a_delete_change_only_this_users_memories(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    make_user: Any,
    embedder: Embedder,
) -> None:
    alice, bob = str(make_user("alice@example.com")), str(make_user("bob@example.com"))
    async with open_memory_store(job_settings.database_url, embedder) as store:
        old = await remember(store, uuid.UUID(alice), "User writes Python.")
        gone = await remember(store, uuid.UUID(alice), "User works at Acme.")
        bobs = await remember(store, uuid.UUID(bob), "User writes Python.")
    _, answer = turn(db, alice, said="I switched to Rust and left Acme.")
    model = Scripted(
        [
            MemoryOp(op="update", id=str(old), content="User writes Rust.", category="fact"),
            MemoryOp(op="delete", id=str(gone), content=None, category=None),
            # Bob's memory was not shown to the model, so naming it must do nothing.
            MemoryOp(op="update", id=str(bobs), content="Hijacked.", category="fact"),
        ]
    )

    await run_job(session_factory, job_settings, alice, answer, model)

    assert [m.content for m in await memories_of(job_settings, alice, embedder)] == [
        "User writes Rust."
    ]
    assert [m.content for m in await memories_of(job_settings, bob, embedder)] == [
        "User writes Python."
    ]


async def test_the_model_is_shown_the_users_related_memories_and_nobody_elses(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    make_user: Any,
    embedder: Embedder,
) -> None:
    alice, bob = str(make_user("alice@example.com")), str(make_user("bob@example.com"))
    async with open_memory_store(job_settings.database_url, embedder) as store:
        await remember(store, uuid.UUID(alice), "User writes Python for work.")
        await remember(store, uuid.UUID(bob), "User secretly writes Python CANARY-91.")
    _, answer = turn(db, alice, said="I write Python all day.")
    model = Scripted()

    await run_job(session_factory, job_settings, alice, answer, model)

    assert "User writes Python for work." in model.prompts[0]
    assert "CANARY-91" not in model.prompts[0]


async def test_spend_is_recorded_without_counting_a_message(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
) -> None:
    _, answer = turn(db, user)

    await run_job(session_factory, job_settings, user, answer)

    row = db.execute(
        "SELECT prompt_tokens, cost_usd, requests FROM usage_daily WHERE user_id = %s", (user,)
    ).fetchone()
    assert row is not None
    assert row[0] > 0  # the model's input and the embeddings
    assert row[1] > Decimal(0)
    assert row[2] == 0  # a background job is not "a message sent"


async def test_a_model_that_fails_leaves_no_memory_and_lets_the_queue_retry(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    embedder: Embedder,
) -> None:
    class Broken:
        def with_structured_output(self, schema: Any, **options: Any) -> Runnable[Any, Any]:
            def fail(prompt: Any) -> Any:
                raise RuntimeError("provider down")

            return RunnableLambda(fail)

    _, answer = turn(db, user)

    with pytest.raises(RuntimeError, match="provider down"):
        await run_job(session_factory, job_settings, user, answer, Broken())

    assert await memories_of(job_settings, user, embedder) == []


def test_the_job_is_registered_with_a_timeout_and_attempts() -> None:
    spec = handlers.JOBS["extract_memories"]

    assert spec.handler is handlers.extract_memories
    assert (spec.timeout_s, spec.max_attempts) == (120, 2)


class SpyTracer:
    """Stands in for the Langfuse tracer: records what the job asks to trace."""

    def __init__(self) -> None:
        self.traces: list[Any] = []

    def run_config(self, trace: Any) -> dict[str, Any]:
        self.traces.append(trace)
        return {"callbacks": [], "run_name": trace.name, "tags": [], "metadata": {}}


async def test_the_job_is_traced_with_a_hashed_user_and_no_chat_trace_id(
    session_factory: Factory,
    job_settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
) -> None:
    conversation, answer = turn(db, user)
    tracer = SpyTracer()
    context = JobContext(
        factory=session_factory,
        settings=job_settings,
        tracer=tracer,  # type: ignore[arg-type]
    )
    async with session_factory() as session, session.begin():
        await queue.enqueue(
            session,
            "extract_memories",
            {"assistant_message_id": answer},
            f"mem:{answer}",
            user_id=uuid.UUID(user),
        )
    job = await queue.claim(session_factory, "w1")
    assert job is not None

    await handlers.JOBS[job.kind].handler(context, job)

    (trace,) = tracer.traces
    assert trace.name == "job.extract_memories"
    assert trace.features == ("memory",)
    assert trace.conversation_id == conversation
    assert user not in trace.user_hash  # a hash of the id, never the id
    assert [(p.name, p.version >= 1) for p in trace.prompts] == [("nexus.memory_extract", True)]
    # The chat run's trace is derived from the assistant message id: this job must not reuse it.
    assert trace.assistant_message_id is None
