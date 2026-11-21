"""Start of every run: reset the per-run fields and fetch what the late context message shows.

Memories are fetched here once long-term memory exists (M5); until then the list is empty. The
user's recent ready files come from an injected lookup (a database read in production).
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.graph.nodes.types import ChatNode
from app.graph.state import ChatState, RecentFile

RecentFiles = Callable[[uuid.UUID], Awaitable[list[RecentFile]]]


def make_load_context(recent_files: RecentFiles | None = None) -> ChatNode:
    async def load_context(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        files: list[RecentFile] = []
        if recent_files is not None:
            # The owner comes from the trusted run config, never from message content.
            user_id = uuid.UUID(str(config["configurable"]["user_id"]))
            files = await recent_files(user_id)
        return {
            "memories": [],
            "recent_files": files,
            "citations": [],
            "tool_calls_this_turn": 0,
        }

    return load_context
