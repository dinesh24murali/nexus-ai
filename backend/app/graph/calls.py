"""The tool calls of the newest AI message, split by the per-turn cap (doc 08 section 2.2).

Both the `approval` node and the `tools` node need the same split, so the calls a user is asked
about are exactly the calls that can run.
"""

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from app.graph.state import ChatState


def calls_of_last_message(state: ChatState) -> list[dict[str, Any]]:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage):
        return []
    return [{**call, "id": str(call["id"])} for call in last.tool_calls]


def split_by_cap(
    state: ChatState, max_calls: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(calls that may run, calls past the cap). The cap counts individual calls."""
    calls = calls_of_last_message(state)
    room = max(0, max_calls - state.get("tool_calls_this_turn", 0))
    return calls[:room], calls[room:]


def disabled_tools(config: RunnableConfig) -> frozenset[str]:
    """Tools the operator switched off for this run (`TOOLS_DISABLED`, a trusted per-run value
    from the run config). Read per run, so a flipped switch also stops a search that was already
    waiting for approval."""
    raw = config.get("configurable", {}).get("disabled_tools") or ()
    return frozenset(str(name) for name in raw)
