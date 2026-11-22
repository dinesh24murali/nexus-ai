"""Assembles the chat graph:

    START -> load_context -> (history above the trigger? summarize) -> agent -> END
                                                                         |  ^
                                         (tool calls) -> [approval ->] tools

The `tools` node exists when at least one tool is given: file search (`retriever`), web search
(`web_searcher`) or the calculator. A message with calls that need the user's approval
(`graph/policy.py`) goes through the `approval` node first, which interrupts the run before any
call of the message runs. Models are injectable so tests use the fake model and the API can pass
factory-built ones.
"""

from typing import Any, Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import Settings
from app.graph.calls import disabled_tools
from app.graph.nodes.agent import make_agent_node
from app.graph.nodes.approval import calls_needing_approval, make_approval_node
from app.graph.nodes.load_context import MemoryLookup, RecentFiles, make_load_context
from app.graph.nodes.summarize import (
    HISTORY_KEEP_TOKENS,
    SUMMARIZE_TRIGGER_TOKENS,
    make_summarize_node,
)
from app.graph.nodes.tools import TOOL_TIMEOUT_S, make_tools_node
from app.graph.state import ChatState
from app.graph.tools.calculator import tool_spec as calculator_spec
from app.graph.tools.search_my_files import tool_spec as search_my_files_spec
from app.graph.tools.web_search import WebSearcher
from app.graph.tools.web_search import tool_spec as web_search_spec
from app.llm.factory import get_model
from app.llm.tokens import count_messages
from app.rag.retriever import Searcher


def build_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None,
    chat_model: Runnable[Any, Any] | None = None,
    summarize_model: Runnable[Any, Any] | None = None,
    retriever: Searcher | None = None,
    web_searcher: WebSearcher | None = None,
    calculator: bool = False,
    recent_files: RecentFiles | None = None,
    memories: MemoryLookup | None = None,
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
    specs: list[StructuredTool] = []
    if retriever is not None:
        specs.append(search_my_files_spec())
    if web_searcher is not None:
        specs.append(web_search_spec())
    if calculator:
        specs.append(calculator_spec())
    available = frozenset(spec.name for spec in specs)
    bound: dict[frozenset[str], Runnable[Any, Any]] = {}

    def tools_for(disabled: frozenset[str]) -> Runnable[Any, Any] | None:
        """The chat model with the tools that are on (not in `disabled`), or None when none is."""
        names = available - disabled
        if not names:
            return None
        if names not in bound:
            subset = [spec for spec in specs if spec.name in names]
            # Factory models bind tools to the primary and the fallback together; an injected
            # model (tests) binds its own.
            if chat_model is None:
                bound[names] = get_model("chat", tools=subset, settings=settings)
            else:
                bound[names] = chat_model.bind_tools(subset)  # type: ignore[attr-defined]
        return bound[names]

    def needs_summary(state: ChatState) -> Literal["summarize", "agent"]:
        too_long = count_messages(state["messages"]) > summarize_trigger_tokens
        return "summarize" if too_long else "agent"

    builder = StateGraph(ChatState)
    builder.add_node("load_context", make_load_context(recent_files, memories))
    builder.add_node("summarize", make_summarize_node(summarizer, keep_tokens=history_keep_tokens))
    builder.add_node("agent", make_agent_node(chat, tools_for=tools_for if specs else None))
    builder.add_edge(START, "load_context")
    builder.add_conditional_edges("load_context", needs_summary, ["summarize", "agent"])
    builder.add_edge("summarize", "agent")
    if not specs:
        builder.add_edge("agent", END)
    else:
        builder.add_node(
            "tools",
            make_tools_node(
                retriever=retriever,
                web_searcher=web_searcher,
                calculator_enabled=calculator,
                timeout_s=tool_timeout_s,
            ),
        )
        builder.add_node("approval", make_approval_node(available=available))

        def after_agent(state: ChatState, config: RunnableConfig) -> str:
            last = state["messages"][-1]
            if not (isinstance(last, AIMessage) and last.tool_calls):
                return END
            on = available - disabled_tools(config)
            return "approval" if calls_needing_approval(state, available=on) else "tools"

        builder.add_conditional_edges("agent", after_agent, ["approval", "tools", END])
        builder.add_edge("approval", "tools")
        builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)
