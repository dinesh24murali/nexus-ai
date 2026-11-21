"""Assembles the chat graph:

    START -> load_context -> (history above the trigger? summarize) -> agent -> END
                                                                         |  ^
                                                       (tool calls) -> tools

The `tools` node exists when a retriever is given (`search_my_files`). Models are injectable so
tests use the fake model and the API can pass factory-built ones.
"""

from typing import Any, Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import Settings
from app.graph.nodes.agent import make_agent_node
from app.graph.nodes.load_context import RecentFiles, make_load_context
from app.graph.nodes.summarize import (
    HISTORY_KEEP_TOKENS,
    SUMMARIZE_TRIGGER_TOKENS,
    make_summarize_node,
)
from app.graph.nodes.tools import TOOL_TIMEOUT_S, make_tools_node
from app.graph.state import ChatState
from app.graph.tools.search_my_files import tool_spec
from app.llm.factory import get_model
from app.llm.tokens import count_messages
from app.rag.retriever import Searcher


def build_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None,
    chat_model: Runnable[Any, Any] | None = None,
    summarize_model: Runnable[Any, Any] | None = None,
    retriever: Searcher | None = None,
    recent_files: RecentFiles | None = None,
    tool_timeout_s: float = TOOL_TIMEOUT_S,
    settings: Settings | None = None,
    summarize_trigger_tokens: int = SUMMARIZE_TRIGGER_TOKENS,
    history_keep_tokens: int = HISTORY_KEEP_TOKENS,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    if summarize_trigger_tokens <= 0 or history_keep_tokens <= 0:
        raise ValueError("summarize thresholds must be positive")
    if history_keep_tokens >= summarize_trigger_tokens:
        raise ValueError("history_keep_tokens must be below summarize_trigger_tokens")

    chat = chat_model or get_model("chat", settings=settings)
    summarizer = summarize_model or get_model("summarize", settings=settings)
    tools_model: Runnable[Any, Any] | None = None
    if retriever is not None:
        # Factory models bind tools to the primary and the fallback together; an injected model
        # (tests) binds its own.
        if chat_model is None:
            tools_model = get_model("chat", tools=[tool_spec()], settings=settings)
        else:
            tools_model = chat_model.bind_tools([tool_spec()])  # type: ignore[attr-defined]

    def needs_summary(state: ChatState) -> Literal["summarize", "agent"]:
        too_long = count_messages(state["messages"]) > summarize_trigger_tokens
        return "summarize" if too_long else "agent"

    builder = StateGraph(ChatState)
    builder.add_node("load_context", make_load_context(recent_files))
    builder.add_node("summarize", make_summarize_node(summarizer, keep_tokens=history_keep_tokens))
    builder.add_node("agent", make_agent_node(chat, tools_model=tools_model))
    builder.add_edge(START, "load_context")
    builder.add_conditional_edges("load_context", needs_summary, ["summarize", "agent"])
    builder.add_edge("summarize", "agent")
    if retriever is None:
        builder.add_edge("agent", END)
    else:
        builder.add_node("tools", make_tools_node(retriever, timeout_s=tool_timeout_s))

        def wants_tools(state: ChatState) -> str:
            last = state["messages"][-1]
            return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

        builder.add_conditional_edges("agent", wants_tools, ["tools", END])
        builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)
