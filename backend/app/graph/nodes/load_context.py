"""Start of every run: reset the per-run fields and fetch what the late context message shows.

The user's long-term memories and recent ready files come from injected lookups (database reads in
production), so the graph never reaches for a store itself and tests can stand in for both.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.graph.nodes.types import ChatNode
from app.graph.state import ChatState, RecentFile

_log = structlog.get_logger("nexus.graph")

RecentFiles = Callable[[uuid.UUID], Awaitable[list[RecentFile]]]

MEMORY_QUERY_CHARS = 2_000  # how much of the user's message is embedded to find memories


@dataclass(frozen=True)
class LoadedMemories:
    """What the memory lookup found: the texts to show, and the embedding tokens it spent."""

    texts: list[str] = field(default_factory=list)
    embedding_tokens: int = 0


MemoryLookup = Callable[[uuid.UUID, str], Awaitable[LoadedMemories]]


def _newest_user_text(state: ChatState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return message.text[:MEMORY_QUERY_CHARS]
    return ""


def make_load_context(
    recent_files: RecentFiles | None = None, memories: MemoryLookup | None = None
) -> ChatNode:
    async def load_context(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        files: list[RecentFile] = []
        remembered: list[str] = []
        # The owner comes from the trusted run config, never from message content.
        user_id = (
            uuid.UUID(str(config["configurable"]["user_id"]))
            if recent_files is not None or memories is not None
            else None
        )
        if recent_files is not None and user_id is not None:
            files = await recent_files(user_id)
        if memories is not None and user_id is not None:
            remembered = await _read_memories(memories, user_id, _newest_user_text(state))
        return {
            "memories": remembered,
            "recent_files": files,
            "citations": [],
            "tool_calls_this_turn": 0,
            "approvals": {},
            "tool_log": [],
        }

    return load_context


async def _read_memories(lookup: MemoryLookup, user_id: uuid.UUID, query: str) -> list[str]:
    """Memories are a convenience: a lookup that fails must not fail the answer. The log carries
    the error type only (an error message could quote a memory)."""
    try:
        loaded = await lookup(user_id, query)
    except Exception as exc:
        _log.warning("memory_read_failed", error_type=type(exc).__name__)
        return []
    if loaded.embedding_tokens:
        # The query embedding is paid for; the run records it (the client never sees this event).
        get_stream_writer()({"kind": "spend", "embedding_tokens": loaded.embedding_tokens})
    return list(loaded.texts)
