"""LLM-T05 end to end: through the graph and the tool, one user never sees another's chunks."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from app.graph.builder import build_graph
from app.llm.fake import FakeChatModel
from app.rag.index import QdrantIndex
from app.rag.retriever import Retriever
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from tests.graph.support import RecordingFake

from .qdrant_support import ALICE, BOB, embedder, ingest

pytestmark = pytest.mark.integration

SECRET = "Quarterly churn fell to 3.1 percent after the Bluebird acquisition"
FIXTURES = Path(__file__).parents[1] / "graph" / "fixtures"


async def search_as(user: uuid.UUID, index: QdrantIndex, fixture: str) -> dict[str, Any]:
    graph = build_graph(
        checkpointer=InMemorySaver(),
        chat_model=FakeChatModel(fixtures_dir=FIXTURES),
        summarize_model=RecordingFake(role="summarize"),
        retriever=Retriever(embedder(), index),
    )
    result: dict[str, Any] = await graph.ainvoke(
        {"messages": [HumanMessage(f"[[fixture:{fixture}]] go")]},
        {"configurable": {"thread_id": str(uuid.uuid4()), "user_id": str(user)}},
    )
    return result


def tool_text(result: dict[str, Any]) -> str:
    return " ".join(m.text for m in result["messages"] if isinstance(m, ToolMessage))


async def test_the_owner_gets_a_citation_for_her_own_file(qdrant_index: QdrantIndex) -> None:
    await ingest(qdrant_index, ALICE, [SECRET], filename="alice-notes.pdf")

    result = await search_as(ALICE, qdrant_index, "rag")

    assert [c["title"] for c in result["citations"]] == ["alice-notes.pdf"]
    assert "Bluebird" in tool_text(result)


async def test_another_user_running_the_same_search_gets_nothing(
    qdrant_index: QdrantIndex,
) -> None:
    await ingest(qdrant_index, ALICE, [SECRET], filename="alice-notes.pdf")

    result = await search_as(BOB, qdrant_index, "rag")

    assert result["citations"] == []
    assert "Bluebird" not in tool_text(result)
    assert "alice-notes" not in tool_text(result)


async def test_naming_the_owners_file_id_does_not_help_another_user(
    qdrant_index: QdrantIndex, tmp_path: Path
) -> None:
    file_id = await ingest(qdrant_index, ALICE, [SECRET], filename="alice-notes.pdf")
    fixtures = tmp_path
    (fixtures / "steal.json").write_text(
        '{"turns": [{"text": "", "tool_calls": [{"name": "search_my_files", "args": '
        f'{{"query": "churn", "file_ids": ["{file_id}"], "user_id": "{ALICE}"}}}}]}},'
        ' {"text": "done"}]}'
    )
    graph = build_graph(
        checkpointer=InMemorySaver(),
        chat_model=FakeChatModel(fixtures_dir=fixtures),
        summarize_model=RecordingFake(role="summarize"),
        retriever=Retriever(embedder(), qdrant_index),
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage("[[fixture:steal]] go")]},
        {"configurable": {"thread_id": "t", "user_id": str(BOB)}},
    )

    assert result["citations"] == []
    assert "Bluebird" not in tool_text(result)
