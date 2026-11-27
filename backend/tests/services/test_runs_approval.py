"""Pausing a run for approval, resuming it, and leaving a usable thread (TASK-023).

A paused run holds no lease: its approval is parked on the conversation and the thread rests at the
interrupt in the checkpoint. Real Postgres, the real checkpointer, the scripted fake model.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from app.api.sse import (
    CitationEvent,
    DoneEvent,
    InterruptEvent,
    RunStartedEvent,
    ToolEndEvent,
    ToolStartEvent,
    UsageEvent,
)
from app.core.errors import ApiError
from app.db.repositories import conversations
from app.graph.state import MAX_TOOL_CALLS
from app.llm.fake import FakeChatModel, _Plan
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import Field

from tests.graph.retrieval_support import StubRetriever
from tests.graph.web_support import StubWeb
from tests.services.support import Env, collect, types

pytestmark = pytest.mark.integration

WEB = "[[fixture:web_search]]"


async def paused(make_env: Any, **options: Any) -> tuple[Env, InterruptEvent, StubWeb]:
    web = options.pop("web", None) or StubWeb()
    env: Env = await make_env(web=web, **options)
    events = await collect(await env.send(f"{WEB} what is new in Go?"))
    interrupt = events[-1]
    assert isinstance(interrupt, InterruptEvent)
    return env, interrupt, web


def tool_calls_of(db: psycopg.Connection[Any], cid: UUID) -> list[dict[str, Any]]:
    row = db.execute(
        "SELECT tool_calls FROM messages WHERE conversation_id = %s AND role = 'assistant'", (cid,)
    ).fetchone()
    assert row
    calls: list[dict[str, Any]] = row[0]
    return calls


# -- pausing --------------------------------------------------------------------------------------


async def test_a_web_search_pauses_the_run_and_parks_the_approval(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, interrupt, web = await paused(make_env)

    assert interrupt.interrupt.tool == "web_search"
    assert interrupt.interrupt.args == {"query": "Go release notes"}
    assert interrupt.interrupt.interrupt_id.startswith("int_")
    assert interrupt.interrupt.expires_at - interrupt.interrupt.created_at == timedelta(hours=24)
    assert web.queries == []  # nothing ran
    # parked on the conversation, with no lease and no answer yet
    assert env.pending() == interrupt.interrupt.model_dump(mode="json")
    assert env.lease() == (None, None)
    assert env.messages() == [("user", f"{WEB} what is new in Go?", "complete", None)]
    # the user message counts as the request; the spend of the paused leg is recorded
    requests, prompt_tokens = env.usage_today()
    assert requests == 1 and prompt_tokens > 0


async def test_the_stream_of_a_paused_run_ends_with_the_interrupt_and_no_usage(
    make_env: Any,
) -> None:
    env: Env = await make_env(web=StubWeb())

    events = await collect(await env.send(f"{WEB} news"))

    assert types(events)[0] == "run_started"
    assert types(events)[-1] == "interrupt"
    assert "usage" not in types(events) and "done" not in types(events)


async def test_the_paused_thread_waits_at_the_approval(make_env: Any) -> None:
    env, _, _ = await paused(make_env)

    state = await env.state()

    assert state.next == ("approval",)
    assert [m.type for m in state.values["messages"]] == ["human", "ai"]


async def test_no_lease_is_held_while_waiting(make_env: Any) -> None:
    env, _, _ = await paused(make_env)

    assert env.service.draining is False
    await env.service.cancel(env.user, env.cid)  # nothing to cancel: not a run any more
    assert env.pending() is not None


# -- resuming -------------------------------------------------------------------------------------


async def test_approve_streams_the_answer_and_clears_the_approval(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, interrupt, web = await paused(make_env)
    before = env.usage_today()

    events = await collect(await env.resume(interrupt.interrupt.interrupt_id, "approve"))

    assert web.queries == ["Go release notes"]
    assert types(events)[0] == "run_started"
    started = events[0]
    assert isinstance(started, RunStartedEvent) and started.user_message_id is None
    assert types(events)[-2:] == ["usage", "done"]
    assert [type(e) for e in events if isinstance(e, ToolStartEvent | ToolEndEvent)] == [
        ToolStartEvent,
        ToolEndEvent,
    ]
    assert any(isinstance(e, CitationEvent) and e.citation.source_type == "web" for e in events)
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.finish_reason == "stop"
    assert env.pending() is None and env.lease() == (None, None)
    answer = env.messages()[-1]
    assert answer[0] == "assistant" and answer[1].startswith("According to the search results")
    stored = tool_calls_of(db, env.cid)
    assert [(c["name"], c["status"], c["args"]) for c in stored] == [
        ("web_search", "completed", {"query": "Go release notes"})
    ]
    # one message sent, one answer: the resume is not a second request, but its spend is added
    requests, prompt_tokens = env.usage_today()
    assert requests == 1 and prompt_tokens > before[1]
    assert db.execute("SELECT count(*) FROM jobs WHERE kind = 'generate_title'").fetchone() == (1,)
    assert [m[0] for m in await env.thread()] == ["human", "ai", "tool", "ai"]


async def test_edit_runs_the_edited_query(make_env: Any, db: psycopg.Connection[Any]) -> None:
    env, interrupt, web = await paused(make_env)

    await collect(
        await env.resume(interrupt.interrupt.interrupt_id, "edit", {"query": "Go 1.24 generics"})
    )

    assert web.queries == ["Go 1.24 generics"]
    assert tool_calls_of(db, env.cid)[0]["args"] == {"query": "Go 1.24 generics"}
    assert env.pending() is None


async def test_reject_runs_nothing_and_the_model_still_answers(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, interrupt, web = await paused(make_env)

    events = await collect(await env.resume(interrupt.interrupt.interrupt_id, "reject"))

    assert web.queries == []
    ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert [e.status for e in ends] == ["rejected"]
    assert types(events)[-2:] == ["usage", "done"]
    assert [(c["status"]) for c in tool_calls_of(db, env.cid)] == ["rejected"]
    assert env.messages()[-1][0] == "assistant"


async def test_a_later_search_in_the_same_turn_keeps_what_the_first_one_found(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    """A file search runs, then a web search waits: the stored answer has both sources and both
    tool calls, although they happened in two different runs."""
    env: Env = await make_env(web=StubWeb(), retriever=StubRetriever())
    first = await collect(await env.send("[[fixture:file_then_web]] compare my churn"))
    interrupt = first[-1]
    assert isinstance(interrupt, InterruptEvent)
    assert interrupt.interrupt.args == {"query": "industry churn benchmark"}
    assert [type(e) for e in first if isinstance(e, ToolEndEvent)] == [ToolEndEvent]  # the file one

    await collect(await env.resume(interrupt.interrupt.interrupt_id, "approve"))

    row = db.execute(
        "SELECT citations, tool_calls FROM messages WHERE role = 'assistant'"
    ).fetchone()
    assert row
    citations, calls = row
    assert [(c["index"], c["source_type"]) for c in citations] == [(1, "file"), (2, "web")]
    assert [(c["name"], c["status"]) for c in calls] == [
        ("search_my_files", "completed"),
        ("web_search", "completed"),
    ]


async def test_two_pauses_in_a_row_park_each_approval(make_env: Any) -> None:
    web = StubWeb()
    env: Env = await make_env(web=web, retriever=StubRetriever())
    # file search, then web search (pause 1); the resume is answered and no further pause follows
    first = await collect(await env.send("[[fixture:file_then_web]] go"))
    pending = first[-1]
    assert isinstance(pending, InterruptEvent)
    assert env.pending() is not None

    await collect(await env.resume(pending.interrupt.interrupt_id, "approve"))

    assert env.pending() is None
    # the next question can pause again with a fresh approval id
    again = await collect(await env.send(f"{WEB} and again"))
    last = again[-1]
    assert isinstance(last, InterruptEvent)
    assert last.interrupt.interrupt_id != pending.interrupt.interrupt_id


async def test_resuming_a_run_can_be_cancelled_and_keeps_the_partial_answer(
    make_env: Any,
) -> None:
    env, interrupt, _ = await paused(make_env, token_delay_s=0.03)
    run = await env.resume(interrupt.interrupt.interrupt_id, "approve")
    async with asyncio.timeout(5):
        while not run.partial:  # noqa: ASYNC110 - wait for the first streamed word
            await asyncio.sleep(0.01)

    await env.service.cancel(env.user, env.cid)
    events = await collect(run)

    done = events[-1]
    assert isinstance(done, DoneEvent) and done.finish_reason == "cancelled"
    assert env.messages()[-1][2] == "cancelled"
    thread = await env.thread()
    assert [t[0] for t in thread] == ["human", "ai", "tool", "ai"]


# -- what a resume refuses -------------------------------------------------------------------------


async def error_of(coro: Any) -> ApiError:
    with pytest.raises(ApiError) as caught:
        await coro
    return caught.value


async def test_nothing_pending_is_a_conflict(make_env: Any) -> None:
    env: Env = await make_env(web=StubWeb())

    error = await error_of(env.resume("int_nope", "approve"))

    assert (error.status_code, error.code) == (409, "NO_PENDING_INTERRUPT")


async def test_another_interrupt_id_is_a_conflict_and_changes_nothing(make_env: Any) -> None:
    env, _, web = await paused(make_env)

    error = await error_of(env.resume("int_someotherid", "approve"))

    assert (error.status_code, error.code) == (409, "NO_PENDING_INTERRUPT")
    assert env.pending() is not None and web.queries == []
    assert env.lease() == (None, None)


async def test_an_expired_approval_cannot_be_resumed(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, interrupt, web = await paused(make_env)
    expired = dict(env.pending() or {})
    expired["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    db.execute(
        "UPDATE conversations SET pending_interrupt = %s::jsonb WHERE id = %s",
        (psycopg.types.json.Jsonb(expired), env.cid),
    )

    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))

    assert (error.status_code, error.code) == (409, "NO_PENDING_INTERRUPT")
    assert web.queries == []
    assert env.pending() is not None  # still shown, as "expired"


@pytest.mark.parametrize(
    "edited", [None, {}, {"query": ""}, {"query": "   "}, {"query": "q" * 401}, {"queries": ["x"]}]
)
async def test_an_edit_needs_a_valid_query_and_leaves_the_approval_waiting(
    make_env: Any, edited: dict[str, Any] | None
) -> None:
    env, interrupt, web = await paused(make_env)

    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "edit", edited))

    assert (error.status_code, error.code) == (422, "VALIDATION_FAILED")
    assert env.pending() is not None and web.queries == []
    assert env.lease() == (None, None)
    # and the user can still decide
    await collect(await env.resume(interrupt.interrupt.interrupt_id, "reject"))
    assert env.pending() is None


async def test_another_users_approval_is_a_404(make_env: Any, make_user: Any) -> None:
    env, interrupt, web = await paused(make_env)
    intruder = UUID(make_user("intruder@example.com"))

    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve", user=intruder))

    assert error.status_code == 404
    assert env.pending() is not None and web.queries == []


async def test_a_double_click_resumes_once(make_env: Any) -> None:
    env, interrupt, web = await paused(make_env, token_delay_s=0.02)
    first = await env.resume(interrupt.interrupt.interrupt_id, "approve")

    second = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))

    assert second.status_code == 409  # run in progress now, no pending approval afterwards
    await collect(first)
    third = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))
    assert (third.status_code, third.code) == (409, "NO_PENDING_INTERRUPT")
    assert web.queries == ["Go release notes"]  # searched once


async def test_while_draining_a_resume_is_refused(make_env: Any) -> None:
    env, interrupt, _ = await paused(make_env)
    env.service.begin_drain()

    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))

    assert (error.status_code, error.code) == (503, "SERVICE_RESTARTING")
    assert env.pending() is not None


async def test_a_resume_counts_toward_the_rate_limit(make_env: Any) -> None:
    env, interrupt, _ = await paused(make_env, settings_update={"chat_rate_limit_per_min": 1})

    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))

    assert (error.status_code, error.code) == (429, "RATE_LIMITED")  # the send used the one slot
    assert env.pending() is not None


# -- leaving a paused conversation ----------------------------------------------------------------


async def test_a_new_message_abandons_the_approval_and_leaves_a_valid_thread(
    make_env: Any,
) -> None:
    env, interrupt, web = await paused(make_env)

    events = await collect(await env.send("never mind, say hello"))

    assert types(events)[-2:] == ["usage", "done"]
    assert env.pending() is None
    assert web.queries == []
    state = await env.state()
    messages = state.values["messages"]
    assert [m.type for m in messages] == ["human", "ai", "tool", "human", "ai"]
    assert isinstance(messages[2], ToolMessage)
    assert messages[2].tool_call_id == messages[1].tool_calls[0]["id"]
    assert "did not run" in messages[2].text
    assert state.next == () and state.interrupts == ()
    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))
    assert (error.status_code, error.code) == (409, "NO_PENDING_INTERRUPT")


async def test_a_thread_left_mid_tool_call_is_repaired_before_the_next_message(
    make_env: Any,
) -> None:
    """The same dangling call can come from a crash or a stop between the model and the tools."""
    env: Env = await make_env(web=StubWeb())
    await collect(await env.send("hello"))
    graph = env.service._graph(None)
    config: RunnableConfig = {"configurable": {"thread_id": str(env.cid)}}
    call = {
        "name": "calculator",
        "args": {"expression": "1+1"},
        "id": "call-x",
        "type": "tool_call",
    }
    await graph.aupdate_state(
        config, {"messages": [AIMessage("", tool_calls=[call])]}, as_node="agent"
    )

    events = await collect(await env.send("are you there?"))

    assert types(events)[-2:] == ["usage", "done"]
    thread = await env.state()
    kinds = [m.type for m in thread.values["messages"]]
    assert kinds == ["human", "ai", "ai", "tool", "human", "ai"]
    assert thread.values["messages"][3].tool_call_id == "call-x"


async def test_a_conversation_without_a_dangling_call_is_left_alone(make_env: Any) -> None:
    env: Env = await make_env(web=StubWeb())

    await collect(await env.send("one"))
    await collect(await env.send("two"))

    assert [t[0] for t in await env.thread()] == ["human", "ai", "human", "ai"]


# -- the tools that need no approval ---------------------------------------------------------------


async def test_a_calculator_call_runs_without_pausing(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env: Env = await make_env(calculator=True)

    events = await collect(await env.send("[[fixture:calc]] what is 17.5% of 2348901?"))

    assert types(events)[-2:] == ["usage", "done"]
    assert env.pending() is None
    assert [(c["name"], c["status"]) for c in tool_calls_of(db, env.cid)] == [
        ("calculator", "completed")
    ]
    usage = next(e for e in events if isinstance(e, UsageEvent))
    assert usage.usage.prompt_tokens > 0
    assert MAX_TOOL_CALLS == 6


# -- edges of pausing and resuming ----------------------------------------------------------------


class AnswersThenSearches(FakeChatModel):
    """Call 1 answers; call 2 asks for a web search; later calls answer again."""

    count: int = Field(default=0)

    def _plan(self, messages: Sequence[BaseMessage]) -> _Plan:
        if self.role != "chat":
            return super()._plan(messages)
        self.count += 1
        if self.count == 2:
            call = {
                "name": "web_search",
                "args": {"query": "regen query"},
                "id": "call_regen",
                "type": "tool_call",
            }
            return _Plan(text="", tool_calls=[call], input_tokens=10, output_tokens=5)
        return self._plain("an answer", 10)


async def test_an_approval_with_an_unreadable_expiry_is_not_acted_on(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, interrupt, web = await paused(make_env)
    broken = dict(env.pending() or {}, expires_at="not a date")
    db.execute(
        "UPDATE conversations SET pending_interrupt = %s WHERE id = %s",
        (psycopg.types.json.Jsonb(broken), env.cid),
    )

    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))

    assert (error.status_code, error.code) == (409, "NO_PENDING_INTERRUPT")
    assert web.queries == []


async def test_losing_the_race_to_take_the_approval_is_a_conflict_and_holds_no_lease(
    make_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, interrupt, web = await paused(make_env)

    async def lost(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(conversations, "take_pending_interrupt", lost)

    error = await error_of(env.resume(interrupt.interrupt.interrupt_id, "approve"))

    assert (error.status_code, error.code) == (409, "RUN_IN_PROGRESS")
    assert env.lease() == (None, None) and web.queries == []


async def test_a_regenerate_that_pauses_drops_the_old_answer_and_resumes_into_the_new_one(
    make_env: Any,
) -> None:
    web = StubWeb()
    env: Env = await make_env(web=web, chat=AnswersThenSearches(role="chat"))
    await collect(await env.send("question"))
    assert [m[0] for m in env.messages()] == ["user", "assistant"]

    events = await collect(await env.service.start_regenerate(env.user, "Marco", env.cid))

    paused_at = events[-1]
    assert isinstance(paused_at, InterruptEvent)
    assert env.messages() == [("user", "question", "complete", None)]  # the old answer is gone
    assert env.pending() is not None and env.lease() == (None, None)
    assert env.usage_today()[0] == 1  # one message sent; a regenerate is not another

    await collect(await env.resume(paused_at.interrupt.interrupt_id, "approve"))

    assert [m[0] for m in env.messages()] == ["user", "assistant"]
    assert web.queries == ["regen query"]
    assert env.usage_today()[0] == 1


async def test_a_run_that_cannot_park_its_approval_fails_visibly_and_frees_the_conversation(
    make_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env: Env = await make_env(web=StubWeb())

    async def broken(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("database went away")

    monkeypatch.setattr(conversations, "park_run", broken)

    events = await collect(await env.send(f"{WEB} news"))

    assert types(events)[-1] == "error"
    assert env.lease() == (None, None)
    assert env.pending() is None


async def test_a_run_whose_lease_was_taken_does_not_park_anything(
    make_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env: Env = await make_env(web=StubWeb())

    async def not_ours(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(conversations, "park_run", not_ours)

    events = await collect(await env.send(f"{WEB} news"))

    assert types(events)[-1] == "error"
    assert env.pending() is None
