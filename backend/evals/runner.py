"""Run one case through the real code paths and measure it.

Chat cases go through the real graph (`build_graph`) with the same usage collector a production
run has, so cost and cached tokens are what a user turn would be billed. The checkpointer is left
out: a case carries its own history, and nothing may leak from one case into the next.
"""

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import openai
import structlog
from app.graph.nodes.summarize import make_summarize_node
from app.llm.pricing import Usage
from app.llm.usage import UsageCollector
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from evals.datasets import Turn

_log = structlog.get_logger("nexus.evals.runner")

CASE_TIMEOUT_S = 120.0
EVAL_USER_ID = "eval-a"  # the fixture user; never a real id


@dataclass
class ChatRun:
    output: str = ""
    ttft_ms: float | None = None
    total_ms: float = 0.0
    usage: Usage = field(default_factory=lambda: Usage(0, 0, 0))
    cost_usd: Decimal = Decimal(0)
    error: str | None = None  # exception class name only: never message text
    # The provider itself rejected the request as unsafe (HTTP 400 "flagged"): no answer exists, so
    # `error` is set too; a case that expects a refusal may count this as one (see run.py).
    blocked: bool = False


def to_messages(turns: Sequence[Turn]) -> list[BaseMessage]:
    return [HumanMessage(t.content) if t.role == "user" else AIMessage(t.content) for t in turns]


def _config(collector: UsageCollector) -> RunnableConfig:
    return cast(
        "RunnableConfig",
        {
            "configurable": {
                "thread_id": str(uuid.uuid4()),
                "user_id": EVAL_USER_ID,
                "user_display_name": "Eval User",
                "today": datetime.now(UTC).date().isoformat(),
            },
            "callbacks": [collector],
        },
    )


async def run_chat(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    *,
    default_model: str,
    text: str,
    history: Sequence[Turn] = (),
    timeout_s: float = CASE_TIMEOUT_S,
) -> ChatRun:
    """One user turn. Never raises: a failure is recorded in `error` and counts against the case."""
    collector = UsageCollector(default_model)
    result = ChatRun()
    started = time.perf_counter()
    answer: str | None = None
    streamed: list[str] = []

    async def drive() -> None:
        nonlocal answer
        request = {"messages": [*to_messages(history), HumanMessage(text)]}
        stream: Any = graph.astream(request, _config(collector), stream_mode=["messages", "values"])
        async for mode, data in stream:
            if mode == "messages":
                chunk, meta = data
                if (
                    isinstance(chunk, AIMessageChunk)
                    and isinstance(meta, dict)
                    and meta.get("langgraph_node") == "agent"
                    and chunk.text
                ):
                    if result.ttft_ms is None:
                        result.ttft_ms = (time.perf_counter() - started) * 1000
                    streamed.append(chunk.text)
            else:
                last = next(
                    (m for m in reversed(data.get("messages", [])) if isinstance(m, AIMessage)),
                    None,
                )
                if last is not None:
                    answer = last.text

    try:
        await asyncio.wait_for(drive(), timeout=timeout_s)
        result.output = answer if answer is not None else "".join(streamed)
        if not result.output.strip():
            result.error = "EmptyAnswer"
    except Exception as exc:
        result.error = type(exc).__name__
        result.blocked = isinstance(exc, openai.OpenAIError) and "flagged" in str(exc).lower()
        _log.warning("eval_chat_failed", error=result.error, blocked=result.blocked)
    result.total_ms = (time.perf_counter() - started) * 1000
    result.usage = collector.total
    result.cost_usd = collector.cost_usd
    if result.ttft_ms is None and result.error is None:
        result.ttft_ms = result.total_ms  # a model that did not stream: the first byte is the last
    return result


async def run_summary(
    model: Runnable[Any, Any], turns: Sequence[Turn], *, timeout_s: float = CASE_TIMEOUT_S
) -> str | None:
    """Summarise every turn of `turns`: a closing user message is appended so the summariser's
    "always keep the newest user message" rule folds the whole case into the summary."""
    node = make_summarize_node(model, keep_tokens=1)
    state: dict[str, Any] = {"messages": [*to_messages(turns), HumanMessage("(continue)")]}
    config = cast("RunnableConfig", {"configurable": {"user_id": EVAL_USER_ID}})
    try:
        update = await asyncio.wait_for(node(cast(Any, state), config), timeout=timeout_s)
    except Exception as exc:
        _log.warning("eval_summary_failed", error=type(exc).__name__)
        return None
    summary = update.get("summary")
    return summary if isinstance(summary, str) and summary else None
