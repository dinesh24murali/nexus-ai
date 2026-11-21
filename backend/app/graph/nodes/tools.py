"""Runs the tool calls of the last AI message (doc 08 section 2.2).

Calls of one message run together, each under its own timeout. Calls count individually toward the
cap of `MAX_TOOL_CALLS` per turn; the ones past the cap are answered with "tool limit reached" so
every `tool_call_id` still gets a ToolMessage. A tool that needs approval (`graph/policy.py`) runs
only with a recorded approve or edit decision: a rejection, and a call nobody decided on, are
answered without running anything (the `approval` node comes first, this is the second lock).
Failures become error results the model can read (never the exception text, which may name
internal hosts). Progress goes to the client through the stream writer as `tool_start`,
`citation`, `tool_end` and an internal `spend` event.
"""

import asyncio
import json
import uuid
from typing import Any

import structlog
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.graph.calls import disabled_tools, split_by_cap
from app.graph.nodes.approval import requires_decision
from app.graph.nodes.types import ChatNode
from app.graph.state import MAX_TOOL_CALLS, ChatState
from app.graph.tools import calculator, search_my_files, web_search
from app.graph.tools.outcome import Pending, ToolOutcome
from app.graph.tools.web_search import WebSearcher
from app.rag.retriever import Searcher

_log = structlog.get_logger("nexus.graph.tools")

TOOL_TIMEOUT_S = 10.0  # doc 08 section 5
LIMIT_TEXT = "tool limit reached"
_LABEL = {
    search_my_files.TOOL_NAME: "The search",
    web_search.TOOL_NAME: "The web search",
    calculator.TOOL_NAME: "The calculation",
}
_DECLINED = (
    "The user declined this web search, so it was not run. Answer without it and say that you "
    "did not search the web."
)
_NOT_APPROVED = "This call was not approved, so it did not run."


def _logged(
    call: dict[str, Any], args: dict[str, Any], status: str, summary: str
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": call["id"],
        "name": call["name"],
        "args": args,
        "status": status,
    }
    if summary:
        entry["summary"] = summary
    return entry


def make_tools_node(
    *,
    retriever: Searcher | None = None,
    web_searcher: WebSearcher | None = None,
    calculator_enabled: bool = False,
    timeout_s: float = TOOL_TIMEOUT_S,
    max_calls: int = MAX_TOOL_CALLS,
) -> ChatNode:
    available = frozenset(
        name
        for name, present in (
            (search_my_files.TOOL_NAME, retriever is not None),
            (web_search.TOOL_NAME, web_searcher is not None),
            (calculator.TOOL_NAME, calculator_enabled),
        )
        if present
    )

    async def phase(
        call: dict[str, Any], args: dict[str, Any], user_id: uuid.UUID
    ) -> Pending | ToolOutcome:
        """One call's first phase: a ToolOutcome for every way it can go wrong."""
        name = call["name"]
        try:
            async with asyncio.timeout(timeout_s):
                if name == search_my_files.TOOL_NAME and retriever is not None:
                    return await search_my_files.search_phase(
                        args, retriever=retriever, user_id=user_id
                    )
                if name == web_search.TOOL_NAME and web_searcher is not None:
                    return await web_search.web_search_phase(args, searcher=web_searcher)
                return await calculator.run_calculator(args)
        except TimeoutError:
            return ToolOutcome(
                f"{_LABEL[name]} timed out. Try again or answer without it.",
                "timed_out",
                "timed out",
            )
        except Exception:
            _log.warning("tool_failed", tool=name, exc_info=True)
            return ToolOutcome(f"{_LABEL[name]} failed. Answer without it.", "failed", "failed")

    async def tools(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            raise TypeError("the tools node needs an AI message with tool calls")
        # The owner comes from the trusted run config, never from the model (LLM-T05).
        user_id = uuid.UUID(str(config["configurable"]["user_id"]))
        write = get_stream_writer()

        used = state.get("tool_calls_this_turn", 0)
        allowed, refused = split_by_cap(state, max_calls)
        decisions = state.get("approvals") or {}
        off = disabled_tools(config)

        # What each allowed call will do: run with these arguments, be declined, or be unknown.
        plans: list[tuple[dict[str, Any], str, dict[str, Any], str | None]] = []
        for call in allowed:
            args: dict[str, Any] = call["args"] if isinstance(call["args"], dict) else {}
            if call["name"] not in available:
                plans.append((call, "unknown", args, None))
            elif call["name"] in off:
                plans.append((call, "disabled", args, None))
            elif not requires_decision(call):
                plans.append((call, "run", args, None))
            else:
                decision = decisions.get(call["id"]) or {}
                kind = decision.get("decision")
                if kind == "approve":
                    plans.append((call, "run", args, None))
                elif (
                    kind == "edit"
                    and isinstance((edited := decision.get("args")), dict)
                    and isinstance(edited.get("query"), str)
                ):
                    prefix = (
                        f"The user changed the search query to {json.dumps(edited['query'])}. "
                        "Results for that query:\n"
                    )
                    plans.append((call, "run", {"query": edited["query"]}, prefix))
                elif kind == "reject":
                    plans.append((call, "declined", args, None))
                else:
                    plans.append((call, "unapproved", args, None))

        for call, action, args, _ in plans:
            if action not in ("unknown", "disabled"):
                write(
                    {
                        "kind": "tool_start",
                        "call_id": call["id"],
                        "name": call["name"],
                        "args": args,
                    }
                )
        phases = iter(
            await asyncio.gather(
                *(phase(c, a, user_id) for c, action, a, _ in plans if action == "run")
            )
        )

        citations = list(state.get("citations") or [])
        log = list(state.get("tool_log") or [])
        results: list[ToolMessage] = []
        for call, action, plan_args, note in plans:  # in call order, so citation numbers follow it
            if action == "disabled":
                results.append(
                    ToolMessage(
                        f"The tool {call['name']} is currently unavailable. Answer without it.",
                        tool_call_id=call["id"],
                        name=call["name"],
                        status="error",
                    )
                )
                continue
            if action == "unknown":
                names = ", ".join(sorted(available - off)) or "none"
                results.append(
                    ToolMessage(
                        f"Unknown tool: {call['name']}. Available tools: {names}.",
                        tool_call_id=call["id"],
                        name=call["name"],
                        status="error",
                    )
                )
                continue
            if action in ("declined", "unapproved"):
                text = _DECLINED if action == "declined" else _NOT_APPROVED
                summary = "Declined by you" if action == "declined" else "Not approved"
                write(
                    {
                        "kind": "tool_end",
                        "call_id": call["id"],
                        "name": call["name"],
                        "status": "rejected",
                        "summary": summary,
                    }
                )
                log.append(_logged(call, plan_args, "rejected", summary))
                results.append(
                    ToolMessage(text, tool_call_id=call["id"], name=call["name"], status="error")
                )
                continue
            first = next(phases)
            outcome = (
                first
                if isinstance(first, ToolOutcome)
                else first.render(first_index=len(citations) + 1)
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
            log.append(_logged(call, plan_args, outcome.status, outcome.summary))
            results.append(
                ToolMessage(
                    (note or "") + outcome.content,
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
            "approvals": {},
            "tool_log": log,
        }

    return tools
