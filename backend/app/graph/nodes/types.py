from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig

from app.graph.state import ChatState


class ChatNode(Protocol):
    """A graph node that also receives the run config (callbacks, trusted per-run values)."""

    async def __call__(self, state: ChatState, config: RunnableConfig) -> dict[str, Any]: ...
