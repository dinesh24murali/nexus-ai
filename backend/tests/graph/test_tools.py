"""The tool loop: search, the cap of six calls, events, failures (doc 08 section 2.2)."""

import asyncio
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from app.graph.builder import build_graph
from app.graph.state import MAX_TOOL_CALLS, RecentFile
from app.rag.retriever import Retrieved
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from tests.graph.retrieval_support import FILE_ID, StubRetriever, passage
from tests.graph.support import RecordingFake, thread

FIXTURES = Path(__file__).parent / "fixtures"
USER = "33333333-3333-4333-8333-333333333333"


class SpyFake(RecordingFake):
    """Records, for every model call, whether tools were bound to the model that made it."""

    has_tools: bool = False
    bound_log: list[bool] = Field(default_factory=list)  # shared by the copies below

    def _plan(self, messages: Sequence[BaseMessage]) -> Any:
        self.bound_log.append(self.has_tools)
        return super()._plan(messages)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> SpyFake:
        return self.model_copy(update={"has_tools": True})


def make(
    retriever: StubRetriever | None = None,
    *,
    fake: SpyFake | None = None,
    **options: Any,
) -> tuple[Any, SpyFake]:
    fake = fake or SpyFake(fixtures_dir=FIXTURES)
    graph = build_graph(
        checkpointer=InMemorySaver(),
        chat_model=fake,
        summarize_model=RecordingFake(role="summarize"),
        retriever=retriever or StubRetriever(),
        **options,
    )
    return graph, fake


async def ask(graph: Any, text: str, **configurable: Any) -> dict[str, Any]:
    result: dict[str, Any] = await graph.ainvoke(
        {"messages": [HumanMessage(text)]}, thread(user_id=USER, **configurable)
    )
    return result


async def events(graph: Any, text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for item in graph.astream(
        {"messages": [HumanMessage(text)]}, thread(user_id=USER), stream_mode="custom"
    ):
        out.append(item)
    return out


def tool_messages(result: dict[str, Any]) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


async def test_a_search_feeds_the_answer_and_collects_the_citation() -> None:
    retriever = StubRetriever()
    graph, _ = make(retriever)

    result = await ask(graph, "[[fixture:rag]] what about churn?")

    assert [m.type for m in result["messages"]] == ["human", "ai", "tool", "ai"]
    assert result["messages"][-1].text.endswith("[1].")
    assert '<document index="1"' in tool_messages(result)[0].text
    assert [c["index"] for c in result["citations"]] == [1]
    assert result["tool_calls_this_turn"] == 1
    assert retriever.calls == [(uuid.UUID(USER), "churn", None)]


async def test_the_next_turn_starts_with_no_citations_and_a_fresh_count() -> None:
    graph, _ = make()
    await ask(graph, "[[fixture:rag]] first question")

    second = await ask(graph, "[[fixture:rag]] second question")

    assert [c["index"] for c in second["citations"]] == [1]
    assert second["tool_calls_this_turn"] == 1


async def test_numbering_continues_across_searches_in_one_turn() -> None:
    retriever = StubRetriever([passage("one", page=1)])
    graph, _ = make(retriever)

    result = await ask(graph, "[[fixture:twice]] go")

    assert [c["index"] for c in result["citations"]] == [1, 2]
    assert '<document index="2"' in tool_messages(result)[1].text


async def test_the_seventh_call_is_refused_and_the_answer_is_forced_without_tools() -> None:
    retriever = StubRetriever()
    graph, fake = make(retriever)

    result = await ask(graph, "[[fixture:cap]] search everything")

    assert len(retriever.calls) == MAX_TOOL_CALLS == 6
    messages = tool_messages(result)
    assert len(messages) == 7
    assert messages[-1].text == "tool limit reached"
    assert messages[-1].status == "error"
    assert all(m.status == "success" for m in messages[:-1])
    assert result["tool_calls_this_turn"] == 6
    assert result["messages"][-1].text == "Done with what I could find."
    # The last model call was made on the model without tools; every earlier one had them.
    assert fake.bound_log[-1] is False
    assert all(fake.bound_log[:-1])


async def test_one_parallel_batch_numbers_its_citations_in_call_order() -> None:
    graph, _ = make(StubRetriever([passage("p", page=1)]))

    result = await ask(graph, "[[fixture:cap]] go")

    first_batch = tool_messages(result)[:4]
    assert [m.text.split('index="')[1].split('"')[0] for m in first_batch] == ["1", "2", "3", "4"]


async def test_tool_events_reach_the_client_in_order() -> None:
    graph, _ = make()

    seen = await events(graph, "[[fixture:rag]] churn?")

    assert [e["kind"] for e in seen] == ["tool_start", "citation", "spend", "tool_end"]
    start, citation, spend, end = seen
    assert start["name"] == "search_my_files" and start["args"] == {"query": "churn"}
    assert start["call_id"] == end["call_id"]
    assert citation["citation"]["index"] == 1
    assert spend["embedding_tokens"] == 7
    assert (end["status"], end["summary"]) == ("completed", "1 passage found")


class SlowRetriever(StubRetriever):
    async def search(self, user_id: uuid.UUID, query: str, file_ids: Any = None) -> Retrieved:
        await asyncio.sleep(5)
        return await super().search(user_id, query, file_ids)


async def test_a_slow_search_times_out_and_the_answer_continues() -> None:
    graph, _ = make(SlowRetriever(), tool_timeout_s=0.05)

    result = await ask(graph, "[[fixture:rag]] churn?")

    assert tool_messages(result)[0].status == "error"
    assert "timed out" in tool_messages(result)[0].text
    assert result["messages"][-1].type == "ai"


async def test_a_timed_out_search_is_reported_as_timed_out() -> None:
    graph, _ = make(SlowRetriever(), tool_timeout_s=0.05)

    seen = await events(graph, "[[fixture:rag]] churn?")

    assert [e["status"] for e in seen if e["kind"] == "tool_end"] == ["timed_out"]


class BrokenRetriever(StubRetriever):
    async def search(self, user_id: uuid.UUID, query: str, file_ids: Any = None) -> Retrieved:
        raise RuntimeError("qdrant at http://secret-host:6333 refused the connection")


async def test_a_failing_search_tells_the_model_without_leaking_the_error() -> None:
    graph, _ = make(BrokenRetriever())

    result = await ask(graph, "[[fixture:rag]] churn?")

    message = tool_messages(result)[0]
    assert message.status == "error"
    assert "secret-host" not in message.text
    assert result["messages"][-1].type == "ai"


async def test_unknown_tools_and_bad_arguments_get_error_results_for_every_call() -> None:
    retriever = StubRetriever()
    graph, _ = make(retriever)

    result = await ask(graph, "[[fixture:badcalls]] go")

    messages = tool_messages(result)
    assert [m.status for m in messages] == ["error", "error"]
    assert "Unknown tool" in messages[0].text
    assert messages[1].text.startswith("Invalid arguments")
    assert retriever.calls == []
    assert result["messages"][-1].type == "ai"


async def test_a_run_without_a_user_id_cannot_search() -> None:
    graph, _ = make()
    with pytest.raises(KeyError):
        await graph.ainvoke(
            {"messages": [HumanMessage("[[fixture:rag]] churn?")]},
            {"configurable": {"thread_id": "t"}},
        )


# --- the late context message lists the recent files ---


async def test_recent_files_are_listed_in_the_context_message() -> None:
    seen_users: list[uuid.UUID] = []

    async def recent(user_id: uuid.UUID) -> list[RecentFile]:
        seen_users.append(user_id)
        return [{"id": str(FILE_ID), "filename": "q3-report.pdf", "uploaded_at": "2026-11-20"}]

    graph, fake = make(recent_files=recent)
    await ask(graph, "hello")

    context = next(
        m.text for m in fake.requests[0] if isinstance(m, HumanMessage) and "<context>" in m.text
    )
    assert f"{FILE_ID} | q3-report.pdf | 2026-11-20" in context
    assert seen_users == [uuid.UUID(USER)]


async def test_no_recent_files_shows_none() -> None:
    graph, fake = make()
    await ask(graph, "hello")

    context = next(
        m.text for m in fake.requests[0] if isinstance(m, HumanMessage) and "<context>" in m.text
    )
    assert "<recent_files>\n(none)\n</recent_files>" in context
