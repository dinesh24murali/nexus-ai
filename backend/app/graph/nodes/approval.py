"""Asks the user before a tool with side effects runs (doc 08 sections 2.2 and 2.4).

The node sits between `agent` and `tools`. When the AI message holds calls that need approval, it
calls `interrupt()` once per such call, in call order; the run stops there and nothing of the
message has run. The decisions go into the state and the `tools` node acts on them, so a message
that mixes tools runs all of them together after the last decision. Anything unclear counts as a
rejection (fail closed). Only calls that can still run are asked about: a call past the turn's cap
or with invalid arguments gets its error result without a card.

When a run resumes, LangGraph runs this node again from the top: an `interrupt()` that already has
its answer returns it at once, the next one pauses. Nothing before the interrupts has side effects.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import ValidationError

from app.graph.calls import disabled_tools, split_by_cap
from app.graph.nodes.types import ChatNode
from app.graph.policy import needs_approval
from app.graph.state import MAX_TOOL_CALLS, ChatState
from app.graph.tools.web_search import TOOL_NAME as WEB_SEARCH
from app.graph.tools.web_search import WebSearchArgs

APPROVAL_TTL = timedelta(hours=24)


def proposal(call: dict[str, Any]) -> dict[str, Any] | None:
    """The arguments to show the user for `call`, or None when the call needs no card because it
    cannot run (invalid arguments): the `tools` node answers it with an error."""
    if call["name"] == WEB_SEARCH:
        try:
            return {"query": WebSearchArgs.model_validate(call["args"]).query}
        except ValidationError:
            return None
    return dict(call["args"]) if isinstance(call["args"], dict) else None


def requires_decision(call: dict[str, Any]) -> bool:
    return needs_approval(call["name"]) and proposal(call) is not None


def calls_needing_approval(
    state: ChatState, *, available: frozenset[str], max_calls: int = MAX_TOOL_CALLS
) -> list[dict[str, Any]]:
    allowed, _ = split_by_cap(state, max_calls)
    return [c for c in allowed if c["name"] in available and requires_decision(c)]


def read_decision(raw: Any, tool: str) -> dict[str, Any]:
    """The user's answer, validated. Whatever is not a clear approve or a valid edit is a reject."""
    if not isinstance(raw, dict):
        return {"decision": "reject"}
    if raw.get("decision") == "approve":
        return {"decision": "approve"}
    if raw.get("decision") == "edit" and tool == WEB_SEARCH:
        try:
            query = WebSearchArgs.model_validate(raw.get("args")).query.strip()
        except ValidationError:
            return {"decision": "reject"}
        if query:
            return {"decision": "edit", "args": {"query": query}}
    return {"decision": "reject"}


def make_approval_node(*, available: frozenset[str], max_calls: int = MAX_TOOL_CALLS) -> ChatNode:
    async def approval(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        decisions: dict[str, dict[str, Any]] = {}
        on = available - disabled_tools(config)
        for call in calls_needing_approval(state, available=on, max_calls=max_calls):
            now = datetime.now(UTC)
            answer = interrupt(
                {
                    "interrupt_id": f"int_{uuid.uuid4().hex[:12]}",
                    "tool": call["name"],
                    "tool_call_id": call["id"],
                    "args": proposal(call),
                    "created_at": now.isoformat(),
                    "expires_at": (now + APPROVAL_TTL).isoformat(),
                }
            )
            decisions[call["id"]] = read_decision(answer, call["name"])
        return {"approvals": decisions}

    return approval
