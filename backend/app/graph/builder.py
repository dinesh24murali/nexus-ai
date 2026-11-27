"""Assembles the chat graph:

    START -> load_context --+
    START -> router --------+-> dispatch -> (history above the trigger? summarize) -> agent -> END
                                  |                                                    |  ^
                                  |                              (tool calls) -> [approval ->] tools
                                  +-> manage_memory [-> forget_confirm] -> END
                                  +-> document_check -> document_gather | document_search (approval)
                                        -> document_compose -> document_persist -> END
                                  +-> research_check -> research_plan -> research_confirm (approval)
                                        -> research_search -> research_report [-> PDF] -> END

`router` (ADR-0010) picks the pipeline for a new message while `load_context` runs; `dispatch`
waits for both and sends `manage_memory`, `generate_document` and `deep_research` messages to
their own pipelines, everything else to the chat agent below. Without a router model or a route
the message is a chat message, which is how the graph behaved before routing existed. A resume
continues inside the pipeline that paused (LangGraph resumes at the interrupted node), so it never
routes again.

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
from app.graph.intents import FALLBACK_INTENT
from app.graph.intents.deep_research import (
    ResearchChecker,
    after,
    make_confirm_node,
    make_plan_node,
    make_report_node,
)
from app.graph.intents.deep_research import make_check_node as make_research_check_node
from app.graph.intents.deep_research import (
    make_search_node as make_research_search_node,
)
from app.graph.intents.generate_document import (
    DocumentService,
    after_check,
    after_compose,
    after_step,
    make_check_node,
    make_compose_node,
    make_gather_node,
    make_persist_node,
    make_search_node,
)
from app.graph.intents.manage_memory import (
    MemoryManager,
    after_manage_memory,
    make_forget_confirm_node,
    make_manage_memory_node,
)
from app.graph.nodes.agent import make_agent_node
from app.graph.nodes.approval import calls_needing_approval, make_approval_node
from app.graph.nodes.load_context import MemoryLookup, RecentFiles, make_load_context
from app.graph.nodes.summarize import (
    HISTORY_KEEP_TOKENS,
    SUMMARIZE_TRIGGER_TOKENS,
    make_summarize_node,
)
from app.graph.nodes.tools import TOOL_TIMEOUT_S, make_tools_node
from app.graph.router import ROUTER_TIMEOUT_S, make_router_node
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
    router_model: Runnable[Any, Any] | None = None,
    memory_manager: MemoryManager | None = None,
    document_service: DocumentService | None = None,
    document_model: Runnable[Any, Any] | None = None,
    research_service: ResearchChecker | None = None,
    research_plan_model: Runnable[Any, Any] | None = None,
    research_model: Runnable[Any, Any] | None = None,
    router_timeout_s: float = ROUTER_TIMEOUT_S,
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

    documents_on = document_service is not None
    pipelines = frozenset(
        {FALLBACK_INTENT}
        | ({"manage_memory"} if memory_manager else set())
        | ({"generate_document"} if documents_on else set())
        | ({"deep_research"} if research_service is not None else set())
    )

    def dispatch_to(
        state: ChatState,
    ) -> Literal["summarize", "agent", "manage_memory", "document_check", "research_check"]:
        route = state.get("route") or {}
        intent = route.get("intent")
        if intent == "manage_memory" and "manage_memory" in pipelines:
            return "manage_memory"
        if intent == "generate_document" and "generate_document" in pipelines:
            return "document_check"
        if intent == "deep_research" and "deep_research" in pipelines:
            return "research_check"
        too_long = count_messages(state["messages"]) > summarize_trigger_tokens
        return "summarize" if too_long else "agent"

    async def dispatch(state: ChatState) -> dict[str, Any]:
        return {}  # a join: it runs once `load_context` and `router` are both done

    builder = StateGraph(ChatState)
    builder.add_node("load_context", make_load_context(recent_files, memories))
    builder.add_node(
        "router", make_router_node(router_model, pipelines=pipelines, timeout_s=router_timeout_s)
    )
    builder.add_node("dispatch", dispatch)
    builder.add_node("summarize", make_summarize_node(summarizer, keep_tokens=history_keep_tokens))
    builder.add_node("agent", make_agent_node(chat, tools_for=tools_for if specs else None))
    builder.add_edge(START, "load_context")
    builder.add_edge(START, "router")
    builder.add_edge(["load_context", "router"], "dispatch")
    destinations = ["summarize", "agent"]
    if memory_manager is not None:
        builder.add_node("manage_memory", make_manage_memory_node(memory_manager))
        builder.add_node("forget_confirm", make_forget_confirm_node(memory_manager))
        builder.add_conditional_edges("manage_memory", after_manage_memory, ["forget_confirm", END])
        builder.add_edge("forget_confirm", END)
        destinations.append("manage_memory")
    if document_service is not None:
        builder.add_node("document_check", make_check_node(document_service))
        builder.add_node("document_gather", make_gather_node(document_service))
        builder.add_node("document_search", make_search_node(web_searcher))
        builder.add_node(
            "document_compose",
            make_compose_node(document_model or get_model("long_form", settings=settings)),
        )
        builder.add_node("document_persist", make_persist_node(document_service))
        builder.add_conditional_edges(
            "document_check", after_check, ["document_gather", "document_search", END]
        )
        builder.add_conditional_edges("document_gather", after_step, ["document_compose", END])
        builder.add_conditional_edges("document_search", after_step, ["document_compose", END])
        builder.add_conditional_edges("document_compose", after_compose, ["document_persist", END])
        builder.add_edge("document_persist", END)
        destinations.append("document_check")
    if research_service is not None:
        builder.add_node(
            "research_check",
            make_research_check_node(research_service, web_searcher, documents=documents_on),
        )
        builder.add_node(
            "research_plan",
            make_plan_node(research_plan_model or get_model("research_plan", settings=settings)),
        )
        builder.add_node("research_confirm", make_confirm_node())
        builder.add_node("research_search", make_research_search_node(web_searcher))
        builder.add_node(
            "research_report",
            make_report_node(
                research_model or get_model("long_form", settings=settings), document_service
            ),
        )
        builder.add_conditional_edges(
            "research_check", after("research_plan"), ["research_plan", END]
        )
        builder.add_conditional_edges(
            "research_plan", after("research_confirm"), ["research_confirm", END]
        )
        builder.add_conditional_edges(
            "research_confirm", after("research_search"), ["research_search", END]
        )
        builder.add_conditional_edges(
            "research_search", after("research_report"), ["research_report", END]
        )
        builder.add_edge("research_report", END)
        destinations.append("research_check")
    builder.add_conditional_edges("dispatch", dispatch_to, destinations)
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
