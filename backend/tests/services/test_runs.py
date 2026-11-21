"""RunService against a real Postgres, the real checkpointer and the scripted fake model."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx2
import openai
import psycopg
import pytest
from app.api.sse import DoneEvent, ErrorEvent, RunStartedEvent, TokenEvent, UsageEvent
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.repositories.messages import assistant_message_id
from app.graph.builder import build_graph
from app.llm.fake import FakeChatModel
from app.services.runs import Run, RunService
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db_support import libpq
from tests.services.support import ExplodingFake, NumberedFake, collect, types

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]
LONG = "[[fixture:long]]"


@dataclass
class Env:
    service: RunService
    saver: AsyncPostgresSaver
    db: psycopg.Connection[Any]
    user: UUID
    cid: UUID
    overrides: list[str | None]

    def messages(self) -> list[tuple[str, str, str, str | None]]:
        rows = self.db.execute(
            "SELECT role, content, status, finish_reason FROM messages"
            " WHERE conversation_id = %s ORDER BY created_at, id",
            (self.cid,),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def lease(self) -> tuple[Any, Any]:
        row = self.db.execute(
            "SELECT active_run_id, run_heartbeat_at FROM conversations WHERE id = %s", (self.cid,)
        ).fetchone()
        assert row
        return row[0], row[1]

    async def thread(self) -> list[tuple[str, str]]:
        graph = build_graph(
            checkpointer=self.saver, chat_model=FakeChatModel(), summarize_model=FakeChatModel()
        )
        state = await graph.aget_state({"configurable": {"thread_id": str(self.cid)}})
        return [(m.type, m.text) for m in state.values.get("messages", [])]

    async def send(self, text: str, *, user: UUID | None = None) -> Run:
        return await self.service.start_send(user or self.user, "Marco", self.cid, text)


@pytest.fixture
async def make_env(
    session_factory: Factory,
    settings: Settings,
    db_url: str,
    db: psycopg.Connection[Any],
    make_user: Any,
) -> AsyncIterator[Callable[..., Any]]:
    stack: list[Any] = []

    async def make(
        *,
        chat: Any = None,
        token_delay_s: float = 0.0,
        heartbeat_s: float = 0.05,
        flags: Any = None,
        tracer: Any = None,
    ) -> Env:
        user = UUID(make_user())
        row = db.execute(
            "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (user,)
        ).fetchone()
        assert row
        ctx = AsyncPostgresSaver.from_conn_string(libpq(db_url))
        saver = await ctx.__aenter__()
        stack.append(ctx)
        overrides: list[str | None] = []

        def graph_factory(model_override: str | None) -> Any:
            overrides.append(model_override)
            return build_graph(
                checkpointer=saver,
                chat_model=chat or FakeChatModel(role="chat", token_delay_s=token_delay_s),
                summarize_model=FakeChatModel(role="summarize"),
            )

        service = RunService(
            factory=session_factory,
            settings=settings,
            graph_factory=graph_factory,
            flags=flags,
            tracer=tracer,
            heartbeat_s=heartbeat_s,
        )
        return Env(service, saver, db, user, UUID(str(row[0])), overrides)

    try:
        yield make
    finally:
        for ctx in stack:
            await ctx.__aexit__(None, None, None)


async def test_a_turn_streams_persists_and_enqueues_the_title(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env: Env = await make_env()

    run = await env.send("hello there")
    events = await collect(run)

    assert types(events)[0] == "run_started"
    assert types(events)[-2:] == ["usage", "done"]
    assert set(types(events)[1:-2]) == {"token"}
    started = events[0]
    assert isinstance(started, RunStartedEvent)
    assert started.assistant_message_id == assistant_message_id(started.run_id)
    streamed = "".join(e.delta for e in events if isinstance(e, TokenEvent))
    assert streamed == "Fake reply: hello there"
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.finish_reason == "stop"
    assert done.message_id == started.assistant_message_id

    assert env.messages() == [
        ("user", "hello there", "complete", None),
        ("assistant", "Fake reply: hello there", "complete", "stop"),
    ]
    assert env.lease() == (None, None)
    stored = db.execute(
        "SELECT id, model, prompt_tokens, completion_tokens, cost_usd FROM messages"
        " WHERE role = 'assistant'"
    ).fetchone()
    assert stored and stored[0] == started.assistant_message_id
    assert stored[2] > 0 and stored[3] > 0 and stored[4] > Decimal(0)
    usage_event = events[-2]
    assert isinstance(usage_event, UsageEvent)
    assert (usage_event.usage.prompt_tokens, usage_event.usage.completion_tokens) == (
        stored[2],
        stored[3],
    )
    daily = db.execute("SELECT requests, prompt_tokens FROM usage_daily").fetchone()
    assert daily == (1, stored[2])
    job = db.execute("SELECT kind, user_id, payload, idempotency_key FROM jobs").fetchone()
    assert job == (
        "generate_title",
        env.user,
        {"conversation_id": str(env.cid)},
        f"title:{env.cid}",
    )
    assert await env.thread() == [("human", "hello there"), ("ai", "Fake reply: hello there")]


async def test_tokens_arrive_one_by_one_not_in_a_single_lump(make_env: Any) -> None:
    env: Env = await make_env()

    events = await collect(await env.send(f"{LONG} go"))

    assert sum(isinstance(e, TokenEvent) for e in events) > 20


async def test_a_second_send_while_a_run_is_active_is_a_conflict(make_env: Any) -> None:
    env: Env = await make_env(token_delay_s=0.02)
    run = await env.send(f"{LONG} first")

    with pytest.raises(ApiError) as exc:
        await env.send("second")

    assert (exc.value.status_code, exc.value.code) == (409, "RUN_IN_PROGRESS")
    assert env.lease()[0] == run.run_id
    await env.service.cancel(env.user, env.cid)
    await collect(run)
    await env.send("third")  # free again


async def test_another_users_conversation_is_a_404_and_stays_untouched(
    make_env: Any, make_user: Any
) -> None:
    env: Env = await make_env()
    intruder = UUID(make_user("intruder@example.com"))

    with pytest.raises(ApiError) as exc:
        await env.send("hi", user=intruder)

    assert exc.value.status_code == 404
    assert env.messages() == []
    assert env.lease() == (None, None)


async def test_an_unknown_conversation_is_a_404(make_env: Any) -> None:
    env: Env = await make_env()

    with pytest.raises(ApiError) as exc:
        await env.service.start_send(env.user, "Marco", UUID(int=1), "hi")

    assert exc.value.status_code == 404


async def test_a_client_that_never_reads_does_not_stop_the_run(make_env: Any) -> None:
    env: Env = await make_env(token_delay_s=0.005)

    run = await env.send(f"{LONG} nobody listens")
    await asyncio.wait_for(run.done.wait(), 10)  # the consumer never touched the queue

    assert [m[2] for m in env.messages()] == ["complete", "complete"]
    assert env.messages()[1][3] == "stop"
    assert env.lease() == (None, None)


async def test_cancel_keeps_the_partial_answer_as_cancelled_and_the_thread_knows_it(
    make_env: Any,
) -> None:
    env: Env = await make_env(token_delay_s=0.02)
    run = await env.send(f"{LONG} stop me")
    seen: list[Any] = []
    while sum(isinstance(e, TokenEvent) for e in seen) < 5:
        event = await asyncio.wait_for(run.events.get(), 5)
        assert event is not None
        seen.append(event)

    assert await env.service.cancel(env.user, env.cid) is True
    seen += await collect(run)

    assert types(seen)[-1] == "done"
    done = seen[-1]
    assert isinstance(done, DoneEvent) and done.finish_reason == "cancelled"
    partial = "".join(e.delta for e in seen if isinstance(e, TokenEvent))
    role, content, status, reason = env.messages()[1]
    assert (role, status, reason) == ("assistant", "cancelled", "cancelled")
    assert content == partial
    assert 0 < len(content) < 300
    assert env.lease() == (None, None)
    assert await env.thread() == [("human", f"{LONG} stop me"), ("ai", partial)]

    await collect(await env.send("and then?"))  # the conversation is usable afterwards
    assert (await env.thread())[-1] == ("ai", "Fake reply: and then?")


async def test_cancelling_before_any_text_leaves_a_usable_conversation(make_env: Any) -> None:
    env: Env = await make_env(token_delay_s=0.3)
    run = await env.send(f"{LONG} slow")
    first = await asyncio.wait_for(run.events.get(), 5)
    assert isinstance(first, RunStartedEvent)

    await env.service.cancel(env.user, env.cid)
    events = await collect(run)

    assert types(events)[-1] == "done"
    assert env.messages()[1][2] == "cancelled"
    await collect(await env.send("hello again"))
    assert (await env.thread())[-2:] == [
        ("human", "hello again"),
        ("ai", "Fake reply: hello again"),
    ]


async def test_cancel_with_nothing_running_or_someone_elses_run_does_nothing(
    make_env: Any, make_user: Any
) -> None:
    env: Env = await make_env(token_delay_s=0.02)
    other = UUID(make_user("other@example.com"))
    assert await env.service.cancel(env.user, env.cid) is False

    run = await env.send(f"{LONG} mine")
    assert await env.service.cancel(other, env.cid) is False  # not their conversation
    assert not run.done.is_set()

    await env.service.cancel(env.user, env.cid)
    await collect(run)


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (
            openai.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            "LLM_UNAVAILABLE",
            True,
        ),
        (
            openai.APITimeoutError(request=httpx2.Request("POST", "https://x")),
            "LLM_UNAVAILABLE",
            True,
        ),
        (RuntimeError("secret internal detail"), "INTERNAL", False),
    ],
)
async def test_a_model_failure_becomes_a_safe_error_event_and_a_failed_message(
    error: Exception, code: str, retryable: bool, make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env: Env = await make_env(chat=ExplodingFake(role="chat", error=error))

    events = await collect(await env.send("hello"))

    assert types(events) == ["run_started", "error"]
    err = events[-1]
    assert isinstance(err, ErrorEvent)
    assert (err.code, err.retryable) == (code, retryable)
    assert "secret" not in err.message
    assert env.messages()[1] == ("assistant", "", "failed", "error")
    assert env.lease() == (None, None)
    assert db.execute("SELECT count(*) FROM jobs").fetchone() == (0,)  # no title for a failed turn


async def test_a_run_that_lost_its_lease_writes_nothing(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    """The reaper took the lease (and wrote its own failed message); the late run must not add a
    second assistant message."""
    env: Env = await make_env(token_delay_s=0.02, heartbeat_s=3600)
    run = await env.send(f"{LONG} slow")
    await asyncio.wait_for(run.events.get(), 5)
    db.execute("UPDATE conversations SET active_run_id = NULL, run_heartbeat_at = NULL")

    events = await collect(run)

    assert types(events)[-1] == "error"
    assert [m[0] for m in env.messages()] == ["user"]


async def test_the_heartbeat_keeps_the_lease_fresh_while_a_run_is_active(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env: Env = await make_env(token_delay_s=0.02, heartbeat_s=0.05)
    run = await env.send(f"{LONG} slow")
    await asyncio.wait_for(run.events.get(), 5)
    db.execute("UPDATE conversations SET run_heartbeat_at = now() - interval '50 seconds'")

    for _ in range(100):
        await asyncio.sleep(0.05)
        row = db.execute(
            "SELECT now() - run_heartbeat_at < interval '5 seconds' FROM conversations"
        ).fetchone()
        if row and row[0]:
            break
    else:
        pytest.fail("the heartbeat never refreshed the lease")

    await env.service.cancel(env.user, env.cid)
    await collect(run)


async def test_while_draining_new_runs_are_refused_but_running_ones_finish(make_env: Any) -> None:
    env: Env = await make_env(token_delay_s=0.01)
    run = await env.send(f"{LONG} in flight")

    env.service.begin_drain()
    with pytest.raises(ApiError) as exc:
        await env.send("late")
    assert (exc.value.status_code, exc.value.code) == (503, "SERVICE_RESTARTING")
    assert exc.value.headers and "Retry-After" in exc.value.headers

    await env.service.drain(timeout_s=10)

    assert run.done.is_set()
    assert env.messages()[1][2:] == ("complete", "stop")


async def test_a_run_still_going_at_the_drain_deadline_is_saved_as_failed(make_env: Any) -> None:
    env: Env = await make_env(token_delay_s=0.05)
    run = await env.send(f"{LONG} too slow")
    while not run.partial:  # noqa: ASYNC110 - polling a plain list the run appends to
        await asyncio.sleep(0.01)

    env.service.begin_drain()
    await env.service.drain(timeout_s=0.1)

    assert run.done.is_set()
    role, content, status, reason = env.messages()[1]
    assert (role, status, reason) == ("assistant", "failed", "server_restart")
    assert content.startswith("This is")  # the partial text survived
    assert env.lease() == (None, None)


async def test_regenerate_replaces_the_last_answer_and_forks_the_thread(make_env: Any) -> None:
    env: Env = await make_env(chat=NumberedFake(role="chat"))
    await collect(await env.send("question"))
    first_id = env.db.execute("SELECT id FROM messages WHERE role = 'assistant'").fetchone()
    assert first_id

    run = await env.service.start_regenerate(env.user, "Marco", env.cid)
    events = await collect(run)

    started = events[0]
    assert isinstance(started, RunStartedEvent) and started.user_message_id is None
    assert types(events)[-2:] == ["usage", "done"]
    assert env.messages() == [
        ("user", "question", "complete", None),
        ("assistant", "answer 2", "complete", "stop"),
    ]
    new_id = env.db.execute("SELECT id FROM messages WHERE role = 'assistant'").fetchone()
    assert new_id and new_id != first_id
    assert await env.thread() == [("human", "question"), ("ai", "answer 2")]

    await collect(await env.service.start_regenerate(env.user, "Marco", env.cid))  # and again
    assert await env.thread() == [("human", "question"), ("ai", "answer 3")]

    await collect(await env.send("follow-up"))  # a later turn continues from the fork
    assert await env.thread() == [
        ("human", "question"),
        ("ai", "answer 3"),
        ("human", "follow-up"),
        ("ai", "answer 4"),
    ]


async def test_regenerate_needs_a_final_assistant_message(make_env: Any) -> None:
    env: Env = await make_env()

    with pytest.raises(ApiError) as empty:
        await env.service.start_regenerate(env.user, "Marco", env.cid)
    assert (empty.value.status_code, empty.value.code) == (409, "REGENERATE_NOT_SUPPORTED")
    assert env.lease() == (None, None)

    env.db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content)"
        " VALUES (%s, %s, 'user', 'q')",
        (env.cid, env.user),
    )
    with pytest.raises(ApiError) as only_user:
        await env.service.start_regenerate(env.user, "Marco", env.cid)
    assert only_user.value.code == "REGENERATE_NOT_SUPPORTED"


async def test_regenerate_is_owner_scoped_and_respects_the_lease(
    make_env: Any, make_user: Any
) -> None:
    env: Env = await make_env(token_delay_s=0.02)
    await collect(await env.send("question"))
    other = UUID(make_user("other@example.com"))
    with pytest.raises(ApiError) as exc:
        await env.service.start_regenerate(other, "Marco", env.cid)
    assert exc.value.status_code == 404

    busy = await env.send(f"{LONG} again")
    with pytest.raises(ApiError) as conflict:
        await env.service.start_regenerate(env.user, "Marco", env.cid)
    assert conflict.value.code == "RUN_IN_PROGRESS"
    await env.service.cancel(env.user, env.cid)
    await collect(busy)


async def test_the_chat_model_override_kill_switch_reaches_the_graph(make_env: Any) -> None:
    class Flags:
        async def get(self, name: str) -> str:
            return "gpt-6-luna" if name == "CHAT_MODEL_OVERRIDE" else ""

    env: Env = await make_env(flags=Flags())

    await collect(await env.send("hi"))

    assert env.overrides == ["gpt-6-luna"]


class RecordingTracer:
    """Stands in for `Tracer`: records each run's trace description, adds no callbacks."""

    def __init__(self) -> None:
        self.traces: list[Any] = []

    def run_config(self, trace: Any, **_: Any) -> dict[str, Any]:
        self.traces.append(trace)
        return {}

    def trace_id_for(self, message_id: str) -> str:
        return f"trace-of-{message_id}"


async def test_a_skipped_moderation_check_is_tagged_on_the_runs_trace(
    make_env: Callable[..., Any],
) -> None:
    tracer = RecordingTracer()
    env = await make_env(tracer=tracer)

    await collect(await env.send("hello [[moderation:down]]"))
    await collect(await env.send("a normal message"))

    assert [t.tags for t in tracer.traces] == [("moderation:skipped",), ()]


async def test_the_answer_is_stored_with_the_id_of_the_trace_it_was_exported_under(
    make_env: Callable[..., Any],
) -> None:
    env = await make_env(tracer=RecordingTracer())

    run = await env.send("hello")
    await collect(run)

    row = env.db.execute(
        "SELECT trace_id FROM messages WHERE id = %s", (run.assistant_message_id,)
    ).fetchone()
    assert row == (f"trace-of-{run.assistant_message_id}",)


async def test_without_tracing_no_trace_id_is_stored(make_env: Callable[..., Any]) -> None:
    env = await make_env()

    run = await env.send("hello")
    await collect(run)

    row = env.db.execute(
        "SELECT trace_id FROM messages WHERE id = %s", (run.assistant_message_id,)
    ).fetchone()
    assert row == (None,)
