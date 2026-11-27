"""The graph against the real Postgres checkpointer, as the API will run it (integration)."""

import pytest
from app.graph.builder import build_graph
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from tests.db_support import libpq
from tests.graph.support import RecordingFake, thread

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _strict_msgpack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")  # production setting (.env.example)


async def test_conversation_survives_a_restart(db_url: str) -> None:
    async with AsyncPostgresSaver.from_conn_string(libpq(db_url)) as saver:
        graph = build_graph(
            checkpointer=saver, chat_model=RecordingFake(), summarize_model=RecordingFake()
        )
        await graph.ainvoke({"messages": [HumanMessage("remember me")]}, thread("conv-pg"))

    # A new process: new connection, new saver, new graph, same thread.
    async with AsyncPostgresSaver.from_conn_string(libpq(db_url)) as saver:
        chat = RecordingFake()
        graph = build_graph(checkpointer=saver, chat_model=chat, summarize_model=RecordingFake())
        result = await graph.ainvoke(
            {"messages": [HumanMessage("still there?")]}, thread("conv-pg")
        )

    assert [m.text for m in result["messages"]] == [
        "remember me",
        "Fake reply: remember me",
        "still there?",
        "Fake reply: still there?",
    ]
    assert "remember me" in [m.text for m in chat.requests[0]]


async def test_encrypted_reasoning_blocks_round_trip_through_the_checkpoint(db_url: str) -> None:
    """With store=false a reasoning item replays only if the checkpoint keeps it intact."""
    reasoning = {
        "type": "reasoning",
        "id": "rs_123",
        "summary": [],
        "encrypted_content": "gAAAA" + "x" * 200,
    }
    answer = {"type": "text", "text": "done", "id": "msg_1"}
    async with AsyncPostgresSaver.from_conn_string(libpq(db_url)) as saver:
        graph = build_graph(
            checkpointer=saver, chat_model=RecordingFake(), summarize_model=RecordingFake()
        )
        await graph.ainvoke(
            {"messages": [HumanMessage("q"), AIMessage(content=[reasoning, answer])]},
            thread("conv-reasoning"),
        )
        state = await graph.aget_state(thread("conv-reasoning"))

    stored = state.values["messages"][1]
    assert isinstance(stored, AIMessage)
    assert stored.content == [reasoning, answer]
