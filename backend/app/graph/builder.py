"""Assembles the chat graph (v1, no tools):

    START -> load_context -> (history above the trigger? summarize) -> agent -> END

Models are injectable so tests use the fake model and the API can pass factory-built ones.
"""

from typing import Any, Literal

from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import Settings
from app.graph.nodes.agent import make_agent_node
from app.graph.nodes.load_context import load_context
from app.graph.nodes.summarize import (
    HISTORY_KEEP_TOKENS,
    SUMMARIZE_TRIGGER_TOKENS,
    make_summarize_node,
)
from app.graph.state import ChatState
from app.llm.factory import get_model
from app.llm.tokens import count_messages


def build_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None,
    chat_model: Runnable[Any, Any] | None = None,
    summarize_model: Runnable[Any, Any] | None = None,
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

    def needs_summary(state: ChatState) -> Literal["summarize", "agent"]:
        too_long = count_messages(state["messages"]) > summarize_trigger_tokens
        return "summarize" if too_long else "agent"

    builder = StateGraph(ChatState)
    builder.add_node("load_context", load_context)
    builder.add_node("summarize", make_summarize_node(summarizer, keep_tokens=history_keep_tokens))
    builder.add_node("agent", make_agent_node(chat))
    builder.add_edge(START, "load_context")
    builder.add_conditional_edges("load_context", needs_summary, ["summarize", "agent"])
    builder.add_edge("summarize", "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)
