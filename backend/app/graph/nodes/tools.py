"""Runs the tool calls of the last AI message (doc 08 section 2.2).

Calls of one message run together, each under its own timeout. Calls count individually toward the
cap of `MAX_TOOL_CALLS` per turn; the ones past the cap are answered with "tool limit reached" so
every `tool_call_id` still gets a ToolMessage. Failures become error results the model can read
(never the exception text, which may name internal hosts). Progress goes to the client through the
stream writer as `tool_start`, `citation`, `tool_end` and an internal `spend` event.
"""

import asyncio
import uuid
from typing import Any

import structlog
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.graph.nodes.types import ChatNode
from app.graph.state import MAX_TOOL_CALLS, ChatState
from app.graph.tools.search_my_files import (
    TOOL_NAME,
    Searched,
    ToolOutcome,
    render,
    search_phase,
)
from app.rag.retriever import Searcher

_log = structlog.get_logger("nexus.graph.tools")

TOOL_TIMEOUT_S = 10.0  # doc 08 section 5
LIMIT_TEXT = "tool limit reached"


def make_tools_node(
    retriever: Searcher,
    *,
    timeout_s: float = TOOL_TIMEOUT_S,
    max_calls: int = MAX_TOOL_CALLS,
) -> ChatNode:
    async def search(call: dict[str, Any], user_id: uuid.UUID) -> Searched | ToolOutcome:
        """One call's search phase: a ToolOutcome for every way it can go wrong."""
        try:
            async with asyncio.timeout(timeout_s):
                return await search_phase(call["args"], retriever=retriever, user_id=user_id)
        except TimeoutError:
            return ToolOutcome(
                "The search timed out. Try again or answer without it.", "timed_out", "timed out"
            )
        except Exception:
            _log.warning("search_my_files_failed", exc_info=True)
            return ToolOutcome("The search failed. Answer without it.", "failed", "search failed")

    async def tools(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            raise TypeError("the tools node needs an AI message with tool calls")
        # The owner comes from the trusted run config, never from the model (LLM-T05).
        user_id = uuid.UUID(str(config["configurable"]["user_id"]))
        write = get_stream_writer()

        used = state.get("tool_calls_this_turn", 0)
        calls = [{**c, "id": str(c["id"])} for c in last.tool_calls]
        allowed = calls[: max(0, max_calls - used)]
        refused = calls[len(allowed) :]

        known = [c for c in allowed if c["name"] == TOOL_NAME]
        for call in known:
            write(
                {
                    "kind": "tool_start",
                    "call_id": call["id"],
                    "name": call["name"],
                    "args": call["args"],
                }
            )
        searched = iter(await asyncio.gather(*(search(c, user_id) for c in known)))

        citations = list(state.get("citations") or [])
        results: list[ToolMessage] = []
        for call in allowed:  # in call order, so citation numbers follow it
            if call["name"] != TOOL_NAME:
                results.append(
                    ToolMessage(
                        f"Unknown tool: {call['name']}. Available tools: {TOOL_NAME}.",
                        tool_call_id=call["id"],
                        name=call["name"],
                        status="error",
                    )
                )
                continue
            first = next(searched)
            outcome = (
                first
                if isinstance(first, ToolOutcome)
                else render(first, first_index=len(citations) + 1)
            )
            citations.extend(outcome.citations)
            for citation in outcome.citations:
                write({"kind": "citation", "citation": citation})
            if outcome.embedding_tokens:
                write({"kind": "spend", "embedding_tokens": outcome.embedding_tokens})
            write(
                {
                    "kind": "tool_end",
                    "call_id": call["id"],
                    "name": call["name"],
                    "status": outcome.status,
                    "summary": outcome.summary,
                }
            )
            results.append(
                ToolMessage(
                    outcome.content,
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="success" if outcome.status == "completed" else "error",
                )
            )
        results.extend(
            ToolMessage(LIMIT_TEXT, tool_call_id=c["id"], name=c["name"], status="error")
            for c in refused
        )

        return {
            "messages": results,
            "citations": citations,
            "tool_calls_this_turn": used + len(allowed),
        }

    return tools
