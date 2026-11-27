"""Approval before `web_search` (doc 08 sections 2.2 and 2.4): interrupt, resume, decisions.

The properties that matter, all with the scripted fake model: nothing of the message runs before
the decision, every `tool_call_id` still gets a ToolMessage, and anything unclear counts as "no".
"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.graph.builder import build_graph
from app.graph.nodes.tools import make_tools_node
from app.graph.state import ChatState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from tests.graph.retrieval_support import StubRetriever
from tests.graph.support import RecordingFake, ToolFake, thread
from tests.graph.web_support import StubWeb, web_result

FIXTURES = Path(__file__).parent / "fixtures"
USER = "33333333-3333-4333-8333-333333333333"


def make(
    *, web: StubWeb | None = None, retriever: StubRetriever | None = None, **options: Any
) -> tuple[Any, RunnableConfig]:
    graph = build_graph(
        checkpointer=InMemorySaver(),
        chat_model=ToolFake(fixtures_dir=FIXTURES),
        summarize_model=RecordingFake(role="summarize"),
        retriever=retriever or StubRetriever(),
        web_searcher=web or StubWeb(),
        calculator=True,
        **options,
    )
    return graph, thread(f"conv-{uuid.uuid4()}", user_id=USER)


async def ask(graph: Any, config: RunnableConfig, text: str) -> None:
    await graph.ainvoke({"messages": [HumanMessage(text)]}, config)


async def decide(graph: Any, config: RunnableConfig, decision: Any) -> None:
    await graph.ainvoke(Command(resume=decision), config)


async def pending(graph: Any, config: RunnableConfig) -> list[dict[str, Any]]:
    return [i.value for i in (await graph.aget_state(config)).interrupts]


async def thread_messages(graph: Any, config: RunnableConfig) -> list[Any]:
    return list((await graph.aget_state(config)).values["messages"])


def tool_messages(messages: list[Any]) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage)]


async def test_a_web_search_pauses_the_run_and_nothing_runs() -> None:
    web = StubWeb()
    graph, config = make(web=web)

    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    state = await graph.aget_state(config)
    assert state.next == ("approval",)
    (value,) = await pending(graph, config)
    assert value["tool"] == "web_search"
    assert value["args"] == {"query": "Go release notes"}
    assert value["tool_call_id"] == "call_web_0_0"
    assert value["interrupt_id"].startswith("int_")
    created, expires = (datetime.fromisoformat(value[k]) for k in ("created_at", "expires_at"))
    assert expires - created == timedelta(hours=24)
    assert web.queries == []
    assert tool_messages(await thread_messages(graph, config)) == []


async def test_approve_runs_the_search_with_the_proposed_query() -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    await decide(graph, config, {"decision": "approve"})

    assert web.queries == ["Go release notes"]
    state = await graph.aget_state(config)
    assert state.next == () and state.interrupts == ()
    messages = state.values["messages"]
    assert '<web_result index="1"' in tool_messages(messages)[0].text
    assert messages[-1].text == "The latest Go release focuses on tooling [1]."
    assert [c["source_type"] for c in state.values["citations"]] == ["web"]
    assert state.values["tool_calls_this_turn"] == 1
    assert not state.values.get("approvals")  # decisions are consumed


async def test_edit_runs_the_search_with_the_edited_query_and_tells_the_model() -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    await decide(graph, config, {"decision": "edit", "args": {"query": "Go 1.24 generics"}})

    assert web.queries == ["Go 1.24 generics"]
    (message,) = tool_messages(await thread_messages(graph, config))
    assert message.status == "success"
    assert "Go 1.24 generics" in message.text.split("<web_result")[0]
    assert "<web_result" in message.text
    # The model's own message is not rewritten: only the tool result tells what changed.
    state = await graph.aget_state(config)
    ai_with_call = next(m for m in state.values["messages"] if getattr(m, "tool_calls", None))
    assert ai_with_call.tool_calls[0]["args"] == {"query": "Go release notes"}


async def test_reject_runs_nothing_and_the_model_still_answers() -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    await decide(graph, config, {"decision": "reject"})

    assert web.queries == []
    messages = await thread_messages(graph, config)
    (declined,) = tool_messages(messages)
    assert declined.tool_call_id == "call_web_0_0"
    assert declined.status == "error"
    assert "declined" in declined.text
    assert messages[-1].type == "ai"
    assert not (await graph.aget_state(config)).values.get("citations")


@pytest.mark.parametrize(
    "decision",
    [
        {"decision": "yes"},
        {"decision": "APPROVE"},
        {"decision": None},
        {"decision": ["approve"]},
        "approve",
        42,
        {"decision": "edit"},
        {"decision": "edit", "args": {}},
        {"decision": "edit", "args": {"query": ""}},
        {"decision": "edit", "args": {"query": "q" * 401}},
        {"decision": "edit", "args": {"query": 5}},
        {"decision": "edit", "args": "x"},
    ],
)
async def test_anything_unclear_counts_as_a_rejection(decision: Any) -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    await decide(graph, config, decision)

    assert web.queries == []
    assert "declined" in tool_messages(await thread_messages(graph, config))[0].text


@pytest.mark.parametrize("empty", [{}])
async def test_an_empty_answer_does_not_resume_anything(empty: Any) -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    await decide(graph, config, empty)

    assert web.queries == []
    assert (await graph.aget_state(config)).next == ("approval",)  # still waiting
    assert len(await pending(graph, config)) == 1


async def test_a_mixed_message_runs_no_call_before_the_decision() -> None:
    """GAP-03: `[search_my_files, web_search]` -> neither call runs until the user decides."""
    web, retriever = StubWeb(), StubRetriever()
    graph, config = make(web=web, retriever=retriever)

    await ask(graph, config, "[[fixture:mixed]] compare my churn with the industry")

    assert retriever.calls == [] and web.queries == []
    assert tool_messages(await thread_messages(graph, config)) == []
    (value,) = await pending(graph, config)
    assert value["tool_call_id"] == "call_mixed_0_1"  # the web call, not the file search

    await decide(graph, config, {"decision": "approve"})

    assert [c[1] for c in retriever.calls] == ["churn"]
    assert web.queries == ["industry churn benchmark"]
    state = await graph.aget_state(config)
    messages = tool_messages(list(state.values["messages"]))
    assert [m.tool_call_id for m in messages] == ["call_mixed_0_0", "call_mixed_0_1"]
    assert [(c["index"], c["source_type"]) for c in state.values["citations"]] == [
        (1, "file"),
        (2, "web"),
    ]


async def test_rejecting_in_a_mixed_message_still_answers_every_tool_call_id() -> None:
    web, retriever = StubWeb(), StubRetriever()
    graph, config = make(web=web, retriever=retriever)
    await ask(graph, config, "[[fixture:mixed]] compare my churn with the industry")

    await decide(graph, config, {"decision": "reject"})

    assert web.queries == []
    assert [c[1] for c in retriever.calls] == ["churn"]  # the other call still runs
    messages = tool_messages(await thread_messages(graph, config))
    assert [m.tool_call_id for m in messages] == ["call_mixed_0_0", "call_mixed_0_1"]
    assert [m.status for m in messages] == ["success", "error"]
    assert "declined" in messages[1].text


async def test_a_calculator_call_waits_with_the_search_it_was_sent_with() -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:calcweb]] go")

    assert tool_messages(await thread_messages(graph, config)) == []

    await decide(graph, config, {"decision": "approve"})

    messages = tool_messages(await thread_messages(graph, config))
    assert messages[0].text == "52.5"
    assert web.queries == ["price of gold today"]


async def test_two_searches_in_one_message_are_approved_one_by_one() -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:webtwice]] go")

    (first,) = await pending(graph, config)
    assert first["args"] == {"query": "first query"}

    await decide(graph, config, {"decision": "approve"})

    (second,) = await pending(graph, config)
    assert second["args"] == {"query": "second query"}
    assert second["tool_call_id"] != first["tool_call_id"]
    assert web.queries == []  # nothing runs until every call is decided
    assert tool_messages(await thread_messages(graph, config)) == []

    await decide(graph, config, {"decision": "edit", "args": {"query": "second, edited"}})

    assert web.queries == ["first query", "second, edited"]
    messages = tool_messages(await thread_messages(graph, config))
    assert [m.tool_call_id for m in messages] == ["call_webtwice_0_0", "call_webtwice_0_1"]
    assert (await graph.aget_state(config)).interrupts == ()


async def test_approve_then_reject_for_two_searches() -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:webtwice]] go")
    await decide(graph, config, {"decision": "approve"})

    await decide(graph, config, {"decision": "reject"})

    assert web.queries == ["first query"]
    messages = tool_messages(await thread_messages(graph, config))
    assert [m.status for m in messages] == ["success", "error"]


async def test_a_calculator_alone_never_interrupts() -> None:
    graph, config = make()

    await ask(graph, config, "[[fixture:calc]] what is 17.5% of 2348901?")

    state = await graph.aget_state(config)
    assert state.next == () and state.interrupts == ()
    first, second = tool_messages(list(state.values["messages"]))
    assert (first.status, second.status) == ("error", "success")
    assert second.text == "411057.675"


async def test_search_my_files_alone_never_interrupts() -> None:
    graph, config = make()

    await ask(graph, config, "[[fixture:rag]] churn?")

    assert (await graph.aget_state(config)).interrupts == ()


async def test_a_search_past_the_tool_cap_is_refused_without_asking() -> None:
    web = StubWeb()
    graph, config = make(web=web)

    await ask(graph, config, "[[fixture:capweb]] go")

    state = await graph.aget_state(config)
    assert state.interrupts == ()  # no approval card for a call that cannot run
    assert web.queries == []
    last = tool_messages(list(state.values["messages"]))[-1]
    assert last.text == "tool limit reached"


async def test_a_search_with_invalid_arguments_is_an_error_not_an_approval_card() -> None:
    web = StubWeb()
    graph, config = make(web=web)

    await ask(graph, config, "[[fixture:webbad]] go")

    state = await graph.aget_state(config)
    assert state.interrupts == ()
    assert web.queries == []
    (message,) = tool_messages(list(state.values["messages"]))
    assert message.text.startswith("Invalid arguments")


async def test_the_next_turn_does_not_inherit_an_old_decision() -> None:
    web = StubWeb()
    graph, config = make(web=web)
    await ask(graph, config, "[[fixture:web]] first")
    await decide(graph, config, {"decision": "approve"})

    await ask(graph, config, "[[fixture:web]] second")

    assert (await graph.aget_state(config)).next == ("approval",)  # asks again
    assert len(web.queries) == 1


async def test_the_tools_node_alone_refuses_a_search_nobody_approved() -> None:
    """Defence in depth: even if an edge skipped the approval node, a web search with no recorded
    decision does not run (this is what `tools.approval_enforced` rests on)."""
    web = StubWeb()
    builder = StateGraph(ChatState)
    builder.add_node("tools", make_tools_node(web_searcher=web))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    call = {
        "name": "web_search",
        "args": {"query": "anything"},
        "id": "call-1",
        "type": "tool_call",
    }

    result = await graph.ainvoke(
        {"messages": [HumanMessage("hi"), AIMessage("", tool_calls=[call])]},
        thread(user_id=USER),
    )

    assert web.queries == []
    (message,) = tool_messages(result["messages"])
    assert message.tool_call_id == "call-1"
    assert message.status == "error"
    assert "not approved" in message.text


async def test_a_rejected_search_is_reported_as_rejected_to_the_client() -> None:
    graph, config = make()
    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    seen = [
        e
        async for e in graph.astream(
            Command(resume={"decision": "reject"}), config, stream_mode="custom"
        )
    ]

    assert [e["kind"] for e in seen] == ["tool_start", "tool_end"]
    assert seen[1]["status"] == "rejected"
    assert seen[0]["call_id"] == seen[1]["call_id"] == "call_web_0_0"


async def test_an_approved_search_reports_its_results_to_the_client() -> None:
    graph, config = make(web=StubWeb([web_result(1), web_result(2)]))
    await ask(graph, config, "[[fixture:web]] what is new in Go?")

    seen = [
        e
        async for e in graph.astream(
            Command(resume={"decision": "approve"}), config, stream_mode="custom"
        )
    ]

    assert [e["kind"] for e in seen] == ["tool_start", "citation", "citation", "tool_end"]
    assert seen[0]["args"] == {"query": "Go release notes"}
    assert (seen[-1]["status"], seen[-1]["summary"]) == ("completed", "2 results found")
    assert seen[1]["citation"]["url"] == "https://example1.com/a"


async def test_the_state_keeps_a_log_of_the_turns_tool_calls_for_a_later_resume() -> None:
    """A run that resumes after an approval stores the whole turn's calls, so the state carries
    them (the earlier run's events are gone)."""
    graph, config = make(web=StubWeb())
    await ask(graph, config, "[[fixture:mixed]] go")
    await decide(graph, config, {"decision": "reject"})

    log = (await graph.aget_state(config)).values["tool_log"]

    assert log == [
        {
            "id": "call_mixed_0_0",
            "name": "search_my_files",
            "args": {"query": "churn"},
            "status": "completed",
            "summary": "1 passage found",
        },
        {
            "id": "call_mixed_0_1",
            "name": "web_search",
            "args": {"query": "industry churn benchmark"},
            "status": "rejected",
            "summary": "Declined by you",
        },
    ]


async def test_the_log_starts_empty_each_turn() -> None:
    graph, config = make()
    await ask(graph, config, "[[fixture:rag]] first")

    await ask(graph, config, "[[fixture:rag]] second")

    assert len((await graph.aget_state(config)).values["tool_log"]) == 1
