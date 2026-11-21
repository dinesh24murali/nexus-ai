"""The chat model call. With tools bound it may ask for them; once the turn has used its cap of
tool calls it answers on the model without tools (doc 08 section 2.2)."""

from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableConfig

from app.graph.nodes.types import ChatNode
from app.graph.prompting import build_request
from app.graph.state import MAX_TOOL_CALLS, ChatState

DEFAULT_DISPLAY_NAME = "the user"


def make_agent_node(
    model: Runnable[Any, Any],
    *,
    tools_model: Runnable[Any, Any] | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
) -> ChatNode:
    async def agent(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        # Per-run values come from the trusted config, never from model output (doc 08 section 2.1).
        configurable = config.get("configurable", {})
        request = build_request(
            state["messages"],
            summary=state.get("summary"),
            memories=state.get("memories") or [],
            today=str(configurable.get("today") or datetime.now(UTC).date().isoformat()),
            user_display_name=str(configurable.get("user_display_name") or DEFAULT_DISPLAY_NAME),
            recent_files=state.get("recent_files") or [],
        )
        out_of_calls = state.get("tool_calls_this_turn", 0) >= max_tool_calls
        chosen = model if tools_model is None or out_of_calls else tools_model
        reply = await chosen.ainvoke(request, config)
        if not isinstance(reply, AIMessage):
            raise TypeError(f"chat model returned {type(reply).__name__}, expected AIMessage")
        return {"messages": [reply]}

    return agent
