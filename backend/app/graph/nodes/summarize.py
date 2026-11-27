"""Summarisation: keeps long conversations inside the context budget (docs/02_outputs/08 s2.2, s3).

When the history is above `SUMMARIZE_TRIGGER_TOKENS`, everything except the most recent
`HISTORY_KEEP_TOKENS` is folded into the rolling `summary` and removed from state. The agent sends
the state unmodified (no sliding window), so the request prefix only changes when this node runs,
which keeps prompt caching effective.
"""

from collections.abc import Sequence
from typing import Any, cast

import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.constants import TAG_NOSTREAM

from app.graph.nodes.types import ChatNode
from app.graph.state import ChatState
from app.llm.prompts import load_prompt
from app.llm.tokens import count_message

_log = structlog.get_logger("nexus.graph.summarize")

SUMMARIZE_TRIGGER_TOKENS = 24_000
HISTORY_KEEP_TOKENS = 12_000
_TOOL_RESULT_CHARS = 2_000


def split_for_summary(messages: Sequence[BaseMessage], *, keep_tokens: int) -> int:
    """Index of the first message to keep: everything before it gets summarised.

    The kept part is the longest recent suffix within `keep_tokens`, moved forward to start on a
    human message. A turn (human message, tool calls and results, answer) is therefore never split,
    so no tool result is left without its call. The newest human message is always kept, even if it
    alone exceeds the budget. Returns 0 when there is nothing to remove.
    """
    newest_user = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=None
    )
    if newest_user is None:
        return 0

    total = 0
    boundary = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        total += count_message(messages[i])
        if total > keep_tokens:
            break
        boundary = i

    cut = min(boundary, newest_user)
    while not isinstance(messages[cut], HumanMessage):
        cut += 1
    return cut


def _transcript(messages: Sequence[BaseMessage]) -> str:
    lines: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            lines.append(f"User: {m.text}")
        elif isinstance(m, ToolMessage):
            lines.append(f"Tool result: {m.text[:_TOOL_RESULT_CHARS]}")
        elif isinstance(m, AIMessage):
            calls = "".join(f" [called {c['name']}]" for c in m.tool_calls)
            lines.append(f"Assistant: {m.text}{calls}")
    return "\n".join(lines)


def make_summarize_node(
    model: Runnable[Any, Any], *, keep_tokens: int = HISTORY_KEEP_TOKENS
) -> ChatNode:
    prompt = load_prompt("nexus.summarize")

    async def summarize(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        messages = state["messages"]
        cut = split_for_summary(messages, keep_tokens=keep_tokens)
        if cut == 0:
            return {}
        old = messages[:cut]
        request = prompt.render(
            existing_summary=state.get("summary") or "(none)", messages=_transcript(old)
        )
        # `nostream`: the summary must not reach the client as chat tokens.
        quiet = cast(RunnableConfig, {**config, "tags": [*config.get("tags", []), TAG_NOSTREAM]})
        try:
            reply = await model.ainvoke([HumanMessage(request)], quiet)
        except Exception:
            # Chat matters more than compaction: carry on with the full history.
            _log.warning("summarize_failed", exc_info=True)
            return {}
        summary = reply.text.strip()
        if not summary:
            return {}
        return {
            "summary": summary,
            "messages": [RemoveMessage(id=m.id) for m in old if m.id is not None],
        }

    return summarize
