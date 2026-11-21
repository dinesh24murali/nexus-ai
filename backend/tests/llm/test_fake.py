import json
import time
from pathlib import Path

import pytest
from app.llm.fake import FakeChatModel, FakeModelError, load_fixture
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


async def collect(model: FakeChatModel, messages: list[BaseMessage]) -> list[AIMessageChunk]:
    return [chunk async for chunk in model.astream(messages)]


async def test_default_fixture_echoes_the_newest_user_message() -> None:
    reply = await FakeChatModel().ainvoke([SystemMessage("sys"), HumanMessage("hello there")])
    assert isinstance(reply, AIMessage)
    assert reply.content == "Fake reply: hello there"
    assert not reply.tool_calls


async def test_reply_is_deterministic() -> None:
    messages: list[BaseMessage] = [HumanMessage("same input")]
    first = await FakeChatModel().ainvoke(messages)
    second = await FakeChatModel().ainvoke(messages)
    assert first.content == second.content
    assert first.usage_metadata == second.usage_metadata


async def test_streaming_yields_several_chunks_that_add_up_to_the_reply() -> None:
    model = FakeChatModel()
    chunks = await collect(model, [HumanMessage("one two three")])
    assert len(chunks) > 3
    assert "".join(str(c.content) for c in chunks) == "Fake reply: one two three"


async def test_usage_is_reported_once_and_survives_aggregation() -> None:
    chunks = await collect(FakeChatModel(), [HumanMessage("count my tokens please")])
    assert len([c for c in chunks if c.usage_metadata]) == 1
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk
    usage = merged.usage_metadata
    assert usage is not None
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


async def test_token_delay_slows_streaming() -> None:
    model = FakeChatModel(token_delay_s=0.02)
    started = time.perf_counter()
    chunks = await collect(model, [HumanMessage("a b c d e")])
    elapsed = time.perf_counter() - started
    assert elapsed >= 0.02 * (len(chunks) - 1) * 0.8


async def test_fixture_marker_in_the_newest_user_message_selects_the_script() -> None:
    reply = await FakeChatModel().ainvoke([HumanMessage("[[fixture:long]] tell me a story")])
    assert str(reply.content).startswith("This is a long scripted answer")
    assert len(str(reply.content).split()) > 60


async def test_marker_in_an_older_message_is_ignored() -> None:
    reply = await FakeChatModel().ainvoke(
        [HumanMessage("[[fixture:long]] first"), AIMessage("ok"), HumanMessage("second")]
    )
    assert reply.content == "Fake reply: second"


async def test_unknown_fixture_raises() -> None:
    with pytest.raises(FakeModelError, match="no-such-fixture"):
        await FakeChatModel().ainvoke([HumanMessage("[[fixture:no-such-fixture]]")])


async def test_scripted_tool_call_then_final_answer_follow_the_message_history() -> None:
    model = FakeChatModel()
    user = HumanMessage("[[fixture:web_search]] what is new in Go?")

    first = await model.ainvoke([user])
    assert len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert call["name"] == "web_search"
    assert call["args"] == {"query": "Go release notes"}
    assert call["id"]

    second = await model.ainvoke([user, first, ToolMessage("result text", tool_call_id=call["id"])])
    assert not second.tool_calls
    assert "[1]" in str(second.content)


async def test_tool_calls_stream_as_one_complete_call() -> None:
    chunks = await collect(FakeChatModel(), [HumanMessage("[[fixture:web_search]] go")])
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk
    assert [c["name"] for c in merged.tool_calls] == ["web_search"]
    assert merged.tool_calls[0]["args"] == {"query": "Go release notes"}


async def test_script_does_not_depend_on_instance_state() -> None:
    user = HumanMessage("[[fixture:web_search]] go")
    first = await FakeChatModel().ainvoke([user])
    again = await FakeChatModel().ainvoke([user])  # a fresh instance (e.g. after a resume)
    assert first.tool_calls[0]["name"] == again.tool_calls[0]["name"]


async def test_summarize_role_returns_a_fixed_summary_without_fixtures() -> None:
    reply = await FakeChatModel(role="summarize").ainvoke([HumanMessage("[[fixture:long]] x")])
    assert reply.content == "FAKE-SUMMARY"


async def test_bind_tools_keeps_the_scripted_behaviour() -> None:
    bound = FakeChatModel().bind_tools([])
    reply = await bound.ainvoke([HumanMessage("hi")])
    assert reply.content == "Fake reply: hi"


def test_fixtures_are_validated(tmp_path: Path) -> None:
    (tmp_path / "loops.json").write_text(
        json.dumps({"turns": [{"tool_calls": [{"name": "t", "args": {}}]}]})
    )
    (tmp_path / "empty.json").write_text(json.dumps({"turns": []}))
    with pytest.raises(FakeModelError, match="last turn"):
        load_fixture("loops", directory=tmp_path)
    with pytest.raises(FakeModelError, match="turns"):
        load_fixture("empty", directory=tmp_path)


@pytest.mark.parametrize("fixture_id", ["../secrets", "a/b", ""])
def test_fixture_ids_cannot_escape_the_directory(fixture_id: str) -> None:
    with pytest.raises(FakeModelError, match="invalid fixture id"):
        load_fixture(fixture_id)
