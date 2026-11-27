"""Routing through the run service (TASK-038, TASK-039): the `route_selected` event, what is stored
on the answer, slash commands that need no graph, the kill switches, who may regenerate, and the
forget approval from card to deletion. Real Postgres and checkpointer; scripted models."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from app.api.sse import (
    DocumentCreatedEvent,
    DoneEvent,
    InterruptEvent,
    MemoryUpdatedEvent,
    RouteSelectedEvent,
    TokenEvent,
    UsageEvent,
)
from app.core.errors import ApiError
from app.llm.fake import FakeChatModel, FakeWebSearcher

from tests.graph.memory_support import StubManager
from tests.graph.support import RecordingFake
from tests.services.support import Env, collect, types

pytestmark = pytest.mark.integration

MEMORY_LIST = "[[route:manage_memory]][[slot:memory_action=list]]"


class Flags:
    def __init__(self, **values: str) -> None:
        self.values = values

    async def get(self, name: str) -> str:
        return self.values.get(name, "")


class ExplodingChat(FakeChatModel):
    """A chat model that must not be called (proves a path needs no model)."""

    async def _astream(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the chat model was called")
        yield  # pragma: no cover


def stored(db: psycopg.Connection[Any], env: Env) -> list[tuple[str, str | None, str | None]]:
    """(role, intent, route_source) of every message."""
    rows = db.execute(
        "SELECT role, intent, route_source FROM messages WHERE conversation_id = %s"
        " ORDER BY created_at, id",
        (env.cid,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def job_kinds(db: psycopg.Connection[Any]) -> list[str]:
    return sorted(r[0] for r in db.execute("SELECT kind FROM jobs").fetchall())


async def routed_env(make_env: Any, **options: Any) -> tuple[Env, RecordingFake, StubManager]:
    router = RecordingFake(role="router")
    manager = options.pop("manager", None) or StubManager()
    env: Env = await make_env(router=router, memory_manager=manager, **options)
    manager.add(env.user, "User prefers Go.")
    return env, router, manager


# -- the route event and what is stored ----------------------------------------------------------


async def test_a_chat_message_reports_its_route_and_stores_it(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, router, _ = await routed_env(make_env)

    events = await collect(await env.send("hello"))

    assert types(events)[:2] == ["run_started", "route_selected"]
    routed = events[1]
    assert isinstance(routed, RouteSelectedEvent)
    assert (routed.intent, routed.source, routed.confidence) == ("general_chat", "router", 0.95)
    assert stored(db, env) == [("user", None, None), ("assistant", "general_chat", "router")]
    assert len(router.requests) == 1


async def test_a_router_that_fails_is_reported_as_a_fallback(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, _ = await routed_env(make_env)

    events = await collect(await env.send("[[route:invalid]] hello"))

    assert isinstance(events[1], RouteSelectedEvent) and events[1].source == "fallback"
    assert types(events)[-1] == "done"  # still answered
    assert stored(db, env)[1] == ("assistant", "general_chat", "fallback")


async def test_the_router_kill_switch_is_read_per_run(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    flags = Flags(ROUTER_DISABLED="1")
    env, router, _ = await routed_env(make_env, flags=flags)

    events = await collect(await env.send(f"{MEMORY_LIST} hi"))

    assert isinstance(events[1], RouteSelectedEvent) and events[1].source == "fallback"
    assert router.requests == []
    assert stored(db, env)[1] == ("assistant", "general_chat", "fallback")


async def test_a_disabled_intent_falls_back_to_chat(make_env: Any) -> None:
    env, _, _ = await routed_env(make_env, flags=Flags(INTENTS_DISABLED="manage_memory"))

    events = await collect(await env.send(f"{MEMORY_LIST} hi"))

    assert isinstance(events[1], RouteSelectedEvent)
    assert (events[1].intent, events[1].source) == ("general_chat", "fallback")


async def test_the_prompt_version_of_the_router_is_on_the_trace(make_env: Any) -> None:
    class Tracer:
        def __init__(self) -> None:
            self.traces: list[Any] = []

        def run_config(self, trace: Any, **_: Any) -> dict[str, Any]:
            self.traces.append(trace)
            return {}

        def trace_id_for(self, message_id: str) -> None:
            return None

    tracer = Tracer()
    env, _, _ = await routed_env(make_env, tracer=tracer)

    await collect(await env.send("/memory"))
    await collect(await env.send("hello"))

    assert {p.name for p in tracer.traces[0].prompts} >= {"nexus.route", "nexus.system"}
    assert (
        "route:command" in tracer.traces[0].tags and "intent:manage_memory" in tracer.traces[0].tags
    )
    assert "route:command" not in tracer.traces[1].tags


# -- memory by chat: list and save ---------------------------------------------------------------


async def test_a_memory_list_is_answered_without_the_chat_model_and_stored_as_such(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, _ = await routed_env(make_env, chat=ExplodingChat(role="chat"))

    events = await collect(await env.send(f"{MEMORY_LIST} what do you know about me?"))

    assert types(events) == ["run_started", "route_selected", "token", "usage", "done"]
    assert "User prefers Go." in "".join(e.delta for e in events if isinstance(e, TokenEvent))
    assert stored(db, env)[1] == ("assistant", "manage_memory", "router")
    # the answer is part of the thread, so the next turn's model sees it
    assert any("User prefers Go." in text for kind, text in await env.thread() if kind == "ai")


async def test_saving_by_chat_reports_the_memory_and_skips_extraction(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, manager = await routed_env(make_env)

    events = await collect(
        await env.send(
            "[[route:manage_memory]][[slot:memory_action=save]]"
            "[[slot:memory_content=User's timezone is IST.]] remember it"
        )
    )

    updated = [e for e in events if isinstance(e, MemoryUpdatedEvent)]
    assert [(e.action, e.content) for e in updated] == [("created", "User's timezone is IST.")]
    assert manager.saved == [(env.user, "User's timezone is IST.")]
    assert job_kinds(db) == ["generate_title"]  # no extract_memories: the user did it directly


async def test_an_ordinary_turn_still_queues_extraction(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, _ = await routed_env(make_env)

    await collect(await env.send("hello"))

    assert job_kinds(db) == ["extract_memories", "generate_title"]


async def test_a_memory_command_skips_the_router_model(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, router, manager = await routed_env(make_env)

    events = await collect(await env.send("/MEMORY remember I prefer tea"))

    assert router.requests == []
    assert isinstance(events[1], RouteSelectedEvent)
    assert (events[1].intent, events[1].source, events[1].confidence) == (
        "manage_memory",
        "command",
        None,
    )
    assert manager.saved == [(env.user, "I prefer tea")]
    assert stored(db, env)[1] == ("assistant", "manage_memory", "command")


async def test_memory_writes_switched_off_blocks_a_save_by_chat(make_env: Any) -> None:
    env, _, manager = await routed_env(make_env, flags=Flags(MEMORY_WRITES_DISABLED="1"))

    events = await collect(await env.send("/memory remember I prefer tea"))

    assert manager.saved == []
    assert "switched off" in "".join(e.delta for e in events if isinstance(e, TokenEvent))


# -- commands that need no graph -----------------------------------------------------------------


async def test_help_is_answered_without_a_graph_a_model_or_a_thread(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, router, _ = await routed_env(make_env, chat=ExplodingChat(role="chat"))

    events = await collect(await env.send("/help"))

    assert types(events) == ["run_started", "token", "usage", "done"]  # no route_selected
    text = "".join(e.delta for e in events if isinstance(e, TokenEvent))
    assert "/memory forget" in text and "/pdf" not in text  # only what this build can do
    usage = next(e for e in events if isinstance(e, UsageEvent))
    assert float(usage.usage.cost_usd) == 0 and usage.usage.prompt_tokens == 0
    assert isinstance(events[-1], DoneEvent) and events[-1].finish_reason == "stop"
    assert router.requests == []
    assert env.messages()[1][:2] == ("assistant", text)
    assert stored(db, env)[1] == ("assistant", None, "command")
    assert await env.thread() == []  # the model never sees it
    assert env.lease() == (None, None)
    assert env.usage_today()[0] == 1  # it is a message sent


async def test_a_malformed_command_gets_a_usage_reply_and_no_pipeline(make_env: Any) -> None:
    env, router, manager = await routed_env(make_env, chat=ExplodingChat(role="chat"))

    events = await collect(await env.send("/memory remember"))

    assert "route_selected" not in types(events)
    assert "".join(e.delta for e in events if isinstance(e, TokenEvent)).startswith("Usage:")
    assert router.requests == [] and manager.saved == []


@pytest.mark.parametrize("command", ["/research why?"])
async def test_a_command_for_a_pipeline_that_is_not_built_says_so(
    make_env: Any, command: str
) -> None:
    env, router, _ = await routed_env(make_env, chat=ExplodingChat(role="chat"))

    events = await collect(await env.send(command))

    text = "".join(e.delta for e in events if isinstance(e, TokenEvent))
    assert "not available yet" in text
    assert "route_selected" not in types(events) and router.requests == []


@pytest.mark.parametrize("command", ["/pdf", "/pdf last", "/brief rust"])
async def test_a_document_command_without_the_document_pipeline_says_so(
    make_env: Any, command: str
) -> None:
    """A service whose graph has no document service explains the command instead of letting chat
    answer it as text."""
    env, router, _ = await routed_env(make_env, chat=ExplodingChat(role="chat"))

    events = await collect(await env.send(command))

    assert "not available yet" in "".join(e.delta for e in events if isinstance(e, TokenEvent))
    assert router.requests == []


async def test_a_command_for_a_switched_off_intent_says_so(make_env: Any) -> None:
    env, _, manager = await routed_env(make_env, flags=Flags(INTENTS_DISABLED="manage_memory"))

    events = await collect(await env.send("/memory remember I prefer tea"))

    assert "switched off" in "".join(e.delta for e in events if isinstance(e, TokenEvent))
    assert manager.saved == []


async def test_text_that_only_looks_like_a_command_is_a_chat_message(make_env: Any) -> None:
    env, _, _ = await routed_env(make_env)

    events = await collect(await env.send("/etc/hosts is a file"))

    assert isinstance(events[1], RouteSelectedEvent) and events[1].intent == "general_chat"


# -- regenerate ------------------------------------------------------------------------------------


async def test_only_a_chat_answer_can_be_regenerated(make_env: Any) -> None:
    env, _, _ = await routed_env(make_env)
    await collect(await env.send("hello"))

    again = await collect(await env.service.start_regenerate(env.user, "Marco", env.cid))

    assert "route_selected" not in types(again)  # only a new message is routed
    assert types(again)[0] == "run_started" and types(again)[-1] == "done"


async def test_a_regenerated_answer_keeps_the_route_it_was_given(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, _ = await routed_env(make_env)
    await collect(await env.send("hello"))

    await collect(await env.service.start_regenerate(env.user, "Marco", env.cid))

    assert stored(db, env)[-1] == ("assistant", "general_chat", "router")


@pytest.mark.parametrize("text", [f"{MEMORY_LIST} hi", "/help", "/memory"])
async def test_a_memory_help_or_usage_answer_cannot_be_regenerated(
    make_env: Any, text: str
) -> None:
    env, _, _ = await routed_env(make_env)
    await collect(await env.send(text))

    with pytest.raises(ApiError) as refused:
        await env.service.start_regenerate(env.user, "Marco", env.cid)

    assert (refused.value.status_code, refused.value.code) == (409, "REGENERATE_NOT_SUPPORTED")
    assert env.lease() == (None, None)


@pytest.mark.parametrize("text", [f"{MEMORY_LIST} hi", "/help", "/memory remember"])
async def test_a_later_memory_or_help_answer_does_not_regenerate_an_older_chat_answer(
    make_env: Any, db: psycopg.Connection[Any], text: str
) -> None:
    """The thread still holds an older chat answer; the newest answer is not one. Regenerating
    must refuse instead of quietly replacing the wrong message."""
    env, _, _ = await routed_env(make_env)
    await collect(await env.send("hello"))
    await collect(await env.send(text))
    before = env.messages()

    with pytest.raises(ApiError) as refused:
        await env.service.start_regenerate(env.user, "Marco", env.cid)

    assert refused.value.code == "REGENERATE_NOT_SUPPORTED"
    assert env.messages() == before and env.lease() == (None, None)


# -- forget: the card, the decision, the deletion --------------------------------------------------


async def forget_card(env: Env, manager: StubManager) -> InterruptEvent:
    manager.add(env.user, "User works at Acme.")
    manager.scores = {"User works at Acme.": 0.9, "User prefers Go.": 0.2}
    events = await collect(await env.send("/memory forget works at Acme"))
    card = events[-1]
    assert isinstance(card, InterruptEvent)
    return card


def offered(card: InterruptEvent) -> list[str]:
    return [c["memory_id"] for c in card.interrupt.args["candidates"]]


async def test_a_forget_parks_a_card_and_deletes_nothing_yet(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, manager = await routed_env(make_env)

    card = await forget_card(env, manager)

    assert card.interrupt.tool == "forget_memory"
    assert [c["content"] for c in card.interrupt.args["candidates"]] == ["User works at Acme."]
    assert env.pending() == card.interrupt.model_dump(mode="json")
    assert env.lease() == (None, None)
    assert manager.deleted == [] and len(manager.texts(env.user)) == 2
    assert env.messages() == [("user", "/memory forget works at Acme", "complete", None)]


async def test_approving_deletes_one_memory_and_writes_the_answer(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, manager = await routed_env(make_env)
    card = await forget_card(env, manager)

    events = await collect(
        await env.resume(card.interrupt.interrupt_id, "approve", {"memory_id": offered(card)[0]})
    )

    assert manager.texts(env.user) == ["User prefers Go."]
    assert [(e.action, e.content) for e in events if isinstance(e, MemoryUpdatedEvent)] == [
        ("deleted", "User works at Acme.")
    ]
    assert "route_selected" not in types(events)  # a resume never routes again
    assert types(events)[-1] == "done"
    assert env.messages()[-1][:2] == ("assistant", "Forgot: User works at Acme.")
    assert stored(db, env)[-1] == ("assistant", "manage_memory", "command")
    assert env.pending() is None and env.lease() == (None, None)
    assert "extract_memories" not in job_kinds(db)


async def test_with_a_single_candidate_approve_needs_no_id(make_env: Any) -> None:
    env, _, manager = await routed_env(make_env)
    card = await forget_card(env, manager)

    await collect(await env.resume(card.interrupt.interrupt_id, "approve"))

    assert manager.texts(env.user) == ["User prefers Go."]


async def test_rejecting_deletes_nothing_and_says_so(make_env: Any) -> None:
    env, _, manager = await routed_env(make_env)
    card = await forget_card(env, manager)

    events = await collect(await env.resume(card.interrupt.interrupt_id, "reject"))

    assert manager.deleted == [] and len(manager.texts(env.user)) == 2
    assert "didn't delete anything" in env.messages()[-1][1]
    assert types(events)[-1] == "done"


@pytest.mark.parametrize(
    ("decision", "args"),
    [
        ("edit", {"query": "x"}),
        ("edit", None),
        ("approve", {"memory_id": str(uuid.uuid4())}),  # not one of the offered memories
    ],
)
async def test_an_invalid_decision_is_refused_and_the_card_stays(
    make_env: Any, decision: str, args: dict[str, Any] | None
) -> None:
    env, _, manager = await routed_env(make_env)
    card = await forget_card(env, manager)

    with pytest.raises(ApiError) as refused:
        await env.resume(card.interrupt.interrupt_id, decision, args)

    assert refused.value.status_code == 422
    assert env.pending() == card.interrupt.model_dump(mode="json")  # still waiting
    assert env.lease() == (None, None) and manager.deleted == []


async def test_with_several_candidates_the_choice_must_be_named(make_env: Any) -> None:
    env, _, manager = await routed_env(make_env)
    manager.add(env.user, "User works at Acme.", "User works remotely.")
    manager.scores = {"User works at Acme.": 0.8, "User works remotely.": 0.78}
    card = (await collect(await env.send("/memory forget my job")))[-1]
    assert isinstance(card, InterruptEvent) and len(offered(card)) == 2

    with pytest.raises(ApiError) as refused:
        await env.resume(card.interrupt.interrupt_id, "approve")
    assert refused.value.status_code == 422

    await collect(
        await env.resume(card.interrupt.interrupt_id, "approve", {"memory_id": offered(card)[1]})
    )
    assert manager.texts(env.user) == ["User prefers Go.", "User works at Acme."]


async def test_an_expired_card_cannot_be_answered_and_deletes_nothing(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env, _, manager = await routed_env(make_env)
    card = await forget_card(env, manager)
    expired = {
        **card.interrupt.model_dump(mode="json"),
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    db.execute(
        "UPDATE conversations SET pending_interrupt = %s::jsonb WHERE id = %s",
        (psycopg.types.json.Jsonb(expired), env.cid),
    )

    with pytest.raises(ApiError) as refused:
        await env.resume(card.interrupt.interrupt_id, "approve")

    assert refused.value.code == "NO_PENDING_INTERRUPT" and manager.deleted == []


async def test_a_new_message_abandons_the_card_and_deletes_nothing(make_env: Any) -> None:
    env, _, manager = await routed_env(make_env)
    card = await forget_card(env, manager)

    events = await collect(await env.send("never mind, hello"))

    assert types(events)[-1] == "done"
    assert env.pending() is None and manager.deleted == []
    with pytest.raises(ApiError) as gone:
        await env.resume(card.interrupt.interrupt_id, "approve")
    assert gone.value.code == "NO_PENDING_INTERRUPT"


async def test_the_resume_applies_a_switch_flipped_while_the_card_waited(make_env: Any) -> None:
    flags = Flags()
    env, _, manager = await routed_env(make_env, flags=flags)
    card = await forget_card(env, manager)
    flags.values["MEMORY_WRITES_DISABLED"] = "1"

    await collect(await env.resume(card.interrupt.interrupt_id, "approve"))

    assert manager.deleted == []
    assert "switched off" in env.messages()[-1][1]


# -- documents (TASK-041) ------------------------------------------------------------------------


async def documents_env(make_env: Any, **options: Any) -> Env:
    env: Env = await make_env(
        router=RecordingFake(role="router"),
        memory_manager=StubManager(),
        long_form_documents=True,
        web=FakeWebSearcher(),
        **options,
    )
    return env


def doc_rows(db: psycopg.Connection[Any], env: Env) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT kind, status, title, message_id, conversation_id FROM documents"
        " WHERE user_id = %s ORDER BY created_at",
        (env.user,),
    ).fetchall()


async def test_pdf_makes_a_pending_document_announced_by_the_answer(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)
    await collect(await env.send("Tell me about launch dates"))

    events = await collect(await env.send("/pdf"))

    assert types(events)[:3] == ["run_started", "route_selected", "document_created"]
    routed, created = events[1], events[2]
    assert isinstance(routed, RouteSelectedEvent)
    assert (routed.intent, routed.source) == ("generate_document", "command")
    assert isinstance(created, DocumentCreatedEvent)
    assert created.document.status == "pending" and created.document.kind == "thread_summary"
    assert types(events)[-2:] == ["usage", "done"]
    # The row, tied to this run's assistant message, and its render job.
    ((kind, status, title, message_id, conversation_id),) = doc_rows(db, env)
    assert (kind, status, conversation_id) == ("thread_summary", "pending", env.cid)
    assert message_id == events[0].assistant_message_id  # type: ignore[union-attr]
    assert "render_document" in job_kinds(db)
    # The answer says so, is stored as a document answer and cannot be regenerated.
    assert stored(db, env)[-1] == ("assistant", "generate_document", "command")
    text = env.messages()[-1][1]
    assert title in text
    with pytest.raises(ApiError) as refused:
        await env.service.start_regenerate(env.user, "Marco", env.cid)
    assert refused.value.code == "REGENERATE_NOT_SUPPORTED"


async def test_a_document_command_is_not_mined_for_memories(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)
    await collect(await env.send("hello"))
    assert "extract_memories" in job_kinds(db)  # an ordinary message is
    db.execute("DELETE FROM jobs WHERE kind = 'extract_memories'")

    await collect(await env.send("/pdf"))

    assert "extract_memories" not in job_kinds(db)


async def test_pdf_last_uses_the_previous_answer_with_no_model_call_for_the_document(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)
    await collect(await env.send("hello"))
    first_answer = env.messages()[1][1]

    events = await collect(await env.send("/pdf last"))

    assert "document_created" in types(events)
    ((kind, *_),) = doc_rows(db, env)
    assert kind == "answer"
    row = db.execute("SELECT draft FROM documents").fetchone()
    assert row and first_answer.split()[0] in str(row[0])


async def test_pdf_with_nothing_to_convert_says_so_and_makes_nothing(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)

    events = await collect(await env.send("/pdf last"))

    assert "document_created" not in types(events)
    assert "earlier answer" in env.messages()[-1][1]
    assert doc_rows(db, env) == [] and "render_document" not in job_kinds(db)
    assert stored(db, env)[-1] == ("assistant", "generate_document", "command")  # still a turn


async def test_a_brief_pauses_at_the_search_card_and_the_resume_makes_the_document(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)

    first = await collect(await env.send("/brief Rust 2024"))

    assert types(first)[-1] == "interrupt" and "document_created" not in types(first)
    card = first[-1]
    assert isinstance(card, InterruptEvent) and card.interrupt.tool == "web_search"
    assert card.interrupt.args == {"query": "Rust 2024"}
    assert doc_rows(db, env) == []
    assert env.lease() == (None, None)  # a paused run holds no lease

    second = await collect(
        await env.resume(card.interrupt.interrupt_id, "edit", {"query": "Rust 2024 edition"})
    )

    assert "document_created" in types(second) and types(second)[-1] == "done"
    ((kind, *_),) = doc_rows(db, env)
    assert kind == "news_brief"
    calls = db.execute("SELECT tool_calls FROM messages WHERE role = 'assistant'").fetchall()
    assert "Rust 2024 edition" in str(calls)  # the card's edit is what ran, and it is on the answer
    assert stored(db, env)[-1] == ("assistant", "generate_document", "command")


async def test_rejecting_the_brief_search_makes_nothing(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)
    first = await collect(await env.send("/brief Rust 2024"))
    card = first[-1]
    assert isinstance(card, InterruptEvent)

    await collect(await env.resume(card.interrupt.interrupt_id, "reject"))

    assert doc_rows(db, env) == [] and "render_document" not in job_kinds(db)


async def test_a_router_chosen_document_request_works_like_the_command(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)
    await collect(await env.send("hello"))

    events = await collect(
        await env.send("[[route:generate_document]][[slot:source=thread]] make that a pdf")
    )

    routed = events[1]
    assert isinstance(routed, RouteSelectedEvent) and routed.source == "router"
    assert "document_created" in types(events)
    assert stored(db, env)[-1] == ("assistant", "generate_document", "router")


async def test_the_day_limit_refuses_the_eleventh_document_with_a_reply(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env, settings_update={"documents_per_day": 2})
    await collect(await env.send("hello"))
    for _ in range(2):
        await collect(await env.send("/pdf"))

    events = await collect(await env.send("/pdf"))

    assert "document_created" not in types(events) and types(events)[-1] == "done"
    assert "limit of 2 documents" in env.messages()[-1][1]
    assert len(doc_rows(db, env)) == 2


async def test_a_document_run_is_billed_to_the_user_like_any_other_run(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    env = await documents_env(make_env)
    await collect(await env.send("hello"))
    before = env.usage_today()

    events = await collect(await env.send("/pdf"))

    usage = next(e for e in events if isinstance(e, UsageEvent))
    assert usage.usage.prompt_tokens > 0  # the long_form call is counted
    assert env.usage_today()[1] > before[1]


async def test_a_cancel_before_the_document_is_written_creates_nothing(
    make_env: Any, db: psycopg.Connection[Any]
) -> None:
    import asyncio

    slow = SlowLongForm(role="long_form")
    env = await documents_env(make_env, long_form=slow)
    await collect(await env.send("hello"))

    run = await env.send("/pdf")
    await asyncio.wait_for(slow.started.wait(), 5)
    assert await env.service.cancel(env.user, env.cid)
    events = await collect(run)

    assert events[-1].type == "done" and events[-1].finish_reason == "cancelled"
    assert doc_rows(db, env) == [] and "render_document" not in job_kinds(db)


class SlowLongForm(FakeChatModel):
    """A `long_form` model that waits (so a test can cancel while it composes)."""

    started: Any = None

    def __init__(self, **kwargs: Any) -> None:
        import asyncio

        super().__init__(**kwargs)
        object.__setattr__(self, "started", asyncio.Event())

    async def _astream(self, *args: Any, **kwargs: Any) -> Any:
        self.started.set()
        import asyncio

        await asyncio.sleep(30)
        yield  # pragma: no cover

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        import asyncio

        self.started.set()
        await asyncio.sleep(30)
