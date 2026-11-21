"""The chat model call. Tool binding, approval and the tool loop join in M3/M4."""

from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableConfig

from app.graph.nodes.types import ChatNode
from app.graph.prompting import build_request
from app.graph.state import ChatState

DEFAULT_DISPLAY_NAME = "the user"


def make_agent_node(model: Runnable[Any, Any]) -> ChatNode:
    async def agent(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        # Per-run values come from the trusted config, never from model output (doc 08 section 2.1).
        configurable = config.get("configurable", {})
        request = build_request(
            state["messages"],
            summary=state.get("summary"),
            memories=state.get("memories") or [],
            today=str(configurable.get("today") or datetime.now(UTC).date().isoformat()),
            user_display_name=str(configurable.get("user_display_name") or DEFAULT_DISPLAY_NAME),
        )
        reply = await model.ainvoke(request, config)
        if not isinstance(reply, AIMessage):
            raise TypeError(f"chat model returned {type(reply).__name__}, expected AIMessage")
        return {"messages": [reply]}

    return agent
