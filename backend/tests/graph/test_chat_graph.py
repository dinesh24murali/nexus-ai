from typing import Any

import pytest
from app.graph.builder import build_graph
from app.llm.prompts import load_prompt
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from tests.graph.support import RecordingFake, filler, thread, wire


def make_graph(
    chat: RecordingFake | None = None,
    summarizer: RecordingFake | None = None,
    **options: Any,
) -> tuple[Any, RecordingFake, RecordingFake]:
    chat = chat or RecordingFake()
    summarizer = summarizer or RecordingFake(role="summarize")
    graph = build_graph(
        checkpointer=InMemorySaver(), chat_model=chat, summarize_model=summarizer, **options
    )
    return graph, chat, summarizer


async def say(graph: Any, text: str, *, thread_id: str = "conv-1", **configurable: Any) -> Any:
    return await graph.ainvoke(
        {"messages": [HumanMessage(text)]}, thread(thread_id, **configurable)
    )


def context_index(request: list[BaseMessage]) -> int:
    return next(
        i
        for i, m in enumerate(request)
        if isinstance(m, HumanMessage) and m.text.startswith("<context>")
    )


def texts(messages: list[BaseMessage]) -> list[str]:
    return [m.text for m in messages]


# --- a conversation ---


async def test_a_single_turn_answers_with_the_chat_model() -> None:
    graph, _, _ = make_graph()
    result = await say(graph, "hello")
    assert [m.type for m in result["messages"]] == ["human", "ai"]
    assert result["messages"][-1].text == "Fake reply: hello"


async def test_multi_turn_thread_keeps_history_and_sends_it_to_the_model() -> None:
    graph, chat, _ = make_graph()
    await say(graph, "first")
    await say(graph, "second")
    result = await say(graph, "third")

    assert texts(result["messages"]) == [
        "first",
        "Fake reply: first",
        "second",
        "Fake reply: second",
        "third",
        "Fake reply: third",
    ]
    third_request = chat.requests[2]
    sent = texts(third_request)
    for earlier in ("first", "Fake reply: first", "second", "Fake reply: second", "third"):
        assert earlier in sent


async def test_threads_are_isolated_from_each_other() -> None:
    graph, _, _ = make_graph()
    await say(graph, "secret of thread A", thread_id="A")
    result = await say(graph, "hello from B", thread_id="B")
    assert "secret of thread A" not in " ".join(texts(result["messages"]))


# --- the request the model sees ---


async def test_request_layout_is_system_history_late_context_then_newest_user_message() -> None:
    graph, chat, _ = make_graph()
    await say(graph, "hello", today="2026-11-21", user_display_name="Ada")
    await say(graph, "again", today="2026-11-22", user_display_name="Ada")

    request = chat.requests[1]
    assert isinstance(request[0], SystemMessage)
    assert request[0].text == load_prompt("nexus.system").render()
    assert texts(request[1:3]) == ["hello", "Fake reply: hello"]
    ctx = context_index(request)
    assert ctx == len(request) - 2
    assert request[-1].text == "again"
    assert "2026-11-22" in request[ctx].text
    assert "Ada" in request[ctx].text
    assert isinstance(request[ctx], HumanMessage)  # data, not system authority


async def test_context_and_summary_are_never_stored_in_graph_state() -> None:
    graph, _, _ = make_graph()
    result = await say(graph, "hello", today="2026-11-21")
    assert not any("<context>" in m.text or "<summary>" in m.text for m in result["messages"])


async def test_missing_display_name_and_date_fall_back_to_safe_values() -> None:
    graph, chat, _ = make_graph()
    await say(graph, "hello")
    ctx = chat.requests[0][context_index(chat.requests[0])].text
    assert "The user's display name: the user" in ctx
    assert "Today's date: 20" in ctx


async def test_load_context_resets_per_run_fields() -> None:
    graph, _, _ = make_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage("hi")], "tool_calls_this_turn": 5, "memories": ["stale"]},
        thread(),
    )
    assert result["tool_calls_this_turn"] == 0
    assert result["memories"] == []


# --- cache-friendliness ---


def prefix(request: list[BaseMessage]) -> list[BaseMessage]:
    """Everything before the late context message: the part a prefix cache can reuse."""
    return request[: context_index(request)]


async def test_message_prefix_is_byte_identical_across_consecutive_turns() -> None:
    graph, chat, _ = make_graph()
    # `today` changes between turns: it must only ever show up in the late context message.
    await say(graph, "turn one", today="2026-11-21", user_display_name="Ada")
    await say(graph, "turn two", today="2026-11-22", user_display_name="Ada")
    await say(graph, "turn three", today="2026-11-23", user_display_name="Ada")

    p1, p2, p3 = (wire(prefix(r)) for r in chat.requests)
    assert p2.startswith(p1[:-1])  # p1 is a JSON list: compare without its closing bracket
    assert p3.startswith(p2[:-1])
    assert len(p3) > len(p2) > len(p1)


async def test_prefix_stays_stable_across_turns_after_a_summary_is_written() -> None:
    graph, chat, summarizer = make_graph(summarize_trigger_tokens=300, history_keep_tokens=120)
    for i in range(4):
        await say(graph, f"u{i} {filler(80)}")
    assert summarizer.requests, "setup: the conversation should have been summarised"

    await say(graph, "after summary one")
    await say(graph, "after summary two")
    a, b = (wire(prefix(r)) for r in chat.requests[-2:])
    assert "<summary>" in a.decode()
    assert b.startswith(a[:-1])


# --- summarisation ---


async def test_default_thresholds_trigger_summarisation_above_24k_tokens() -> None:
    graph, _, summarizer = make_graph()
    for i in range(12):
        await say(graph, f"u{i} {filler(1000)}")
    assert not summarizer.requests  # just under 24k tokens of history: not yet "above"

    for i in range(12, 16):
        await say(graph, f"u{i} {filler(1000)}")
    assert len(summarizer.requests) >= 1
    state = (await graph.aget_state(thread())).values
    assert state["summary"] == "FAKE-SUMMARY"


async def test_summarising_replaces_old_messages_with_a_summary_and_keeps_recent_ones() -> None:
    graph, chat, summarizer = make_graph(summarize_trigger_tokens=300, history_keep_tokens=120)
    for i in range(3):
        await say(graph, f"u{i} {filler(40)}")  # ~90 tokens per turn: still under the trigger
    assert not summarizer.requests

    result = await say(graph, f"u3 {filler(40)}")
    assert len(summarizer.requests) == 1
    assert result["summary"] == "FAKE-SUMMARY"
    kept = result["messages"]
    assert isinstance(kept[0], HumanMessage)
    assert kept[-2].text.startswith("u3")  # the newest user message survived
    assert not any(m.text.startswith("u0") for m in kept)  # the oldest turn is gone
    # The summariser was shown what it was asked to compress, wrapped as data.
    shown = summarizer.requests[0][-1].text
    assert "u0" in shown
    assert "<messages>" in shown
    # And the agent request that followed starts with the summary, right after the system prompt.
    summarised_request = chat.requests[-1]
    assert summarised_request[1].text.startswith("<summary>")
    assert "FAKE-SUMMARY" in summarised_request[1].text


async def test_the_summariser_is_not_streamed_to_the_client() -> None:
    graph, _, summarizer = make_graph(summarize_trigger_tokens=300, history_keep_tokens=120)
    for i in range(3):
        await say(graph, f"u{i} {filler(40)}")
    streamed = [
        chunk.text
        async for chunk, _meta in graph.astream(
            {"messages": [HumanMessage(f"u3 {filler(40)}")]}, thread(), stream_mode="messages"
        )
    ]
    assert summarizer.requests, "setup: the summariser must have run"
    assert streamed, "the answer itself streams"
    assert "FAKE-SUMMARY" not in "".join(streamed)


async def test_below_the_threshold_nothing_is_summarised() -> None:
    graph, _, summarizer = make_graph(summarize_trigger_tokens=10_000, history_keep_tokens=4_000)
    for i in range(5):
        await say(graph, f"u{i} {filler(50)}")
    assert not summarizer.requests


async def test_a_second_summary_merges_the_first() -> None:
    graph, _, summarizer = make_graph(summarize_trigger_tokens=300, history_keep_tokens=120)
    for i in range(8):
        await say(graph, f"u{i} {filler(80)}")
    assert len(summarizer.requests) >= 2
    second = summarizer.requests[1][-1].text
    assert "FAKE-SUMMARY" in second  # the existing summary went into the second summarisation


class FailingSummarizer(RecordingFake):
    def _plan(self, messages: Any) -> Any:
        raise RuntimeError("summariser is down")


async def test_a_failing_summariser_does_not_break_the_chat() -> None:
    graph, _, _ = make_graph(
        summarizer=FailingSummarizer(role="summarize"),
        summarize_trigger_tokens=300,
        history_keep_tokens=120,
    )
    for i in range(5):
        result = await say(graph, f"u{i} {filler(80)}")
    assert result["messages"][-1].text.startswith("Fake reply: u4")
    assert len(result["messages"]) == 10  # nothing was removed
    assert not result.get("summary")


@pytest.mark.parametrize("value", [0, -1])
def test_invalid_thresholds_are_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        make_graph(summarize_trigger_tokens=value)
    with pytest.raises(ValueError, match="positive"):
        make_graph(history_keep_tokens=value)


def test_keep_budget_must_be_below_the_trigger() -> None:
    with pytest.raises(ValueError, match="below"):
        make_graph(summarize_trigger_tokens=100, history_keep_tokens=100)


async def test_ai_replies_are_plain_messages_in_state() -> None:
    graph, _, _ = make_graph()
    result = await say(graph, "hi")
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].usage_metadata is not None
