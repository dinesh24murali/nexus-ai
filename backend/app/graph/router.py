"""The intent router (ADR-0010, docs/02_outputs/08 sections 2.1 and 2.2).

`router` runs next to `load_context` at the start of a run and decides which pipeline answers the
newest message. Every way it can fail ends the same way: the message is answered by `general_chat`
(`source: fallback`), so a router problem costs quality and never availability.

Order of decisions:
  1. a slash command in the run config (parsed by code before the graph) -> its intent and slots,
     no model call (`source: command`)
  2. `ROUTER_DISABLED` -> `general_chat`
  3. the router model (strict structured output, 5 s) -> a decision; confidence below
     `min_confidence` (front-matter of `nexus.route`), an intent that is not built or is switched
     off (`INTENTS_DISABLED`), bad slots, an error, a timeout or invalid output -> `general_chat`

The router sees user-authored text only (LLM-T17): the last three user messages and the previous
turn's intent. Never assistant text, tool results, documents or memories, so injected content
cannot steer a message into a pipeline with side effects. The router grants no permission either:
every pipeline keeps its own approval.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Literal, cast

import structlog
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import BaseModel, ConfigDict, ValidationError

from app.graph.commands import MAX_MEMORY_CHARS, MAX_TOPIC_CHARS
from app.graph.intents import FALLBACK_INTENT, INTENTS, PIPELINES, Intent
from app.graph.nodes.types import ChatNode
from app.graph.state import ChatState
from app.llm.prompts import Prompt, load_prompt

_log = structlog.get_logger("nexus.router")

PROMPT_NAME = "nexus.route"
ROUTER_TIMEOUT_S = 5.0  # ADR-0010: a slow router must not hold the answer back
HISTORY_USER_MESSAGES = 3
MESSAGE_CHARS = 1_000  # each user message is cut to this before it reaches the router
NO_INTENT = "none"

RouteSource = Literal["router", "command", "fallback"]


class RouteSlots(BaseModel):
    """The slots as the model returns them. Strict structured output needs every field present, so
    the unused ones are null. No slot carries an id."""

    model_config = ConfigDict(extra="forbid")

    topic: str | None
    source: Literal["thread", "last_answer", "topic"] | None
    output: Literal["chat", "pdf"] | None
    memory_action: Literal["list", "save", "forget"] | None
    memory_content: str | None


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float
    slots: RouteSlots


@dataclass(frozen=True)
class Route:
    """What dispatch acts on, and what the `route_selected` event reports."""

    intent: Intent
    source: RouteSource
    confidence: float | None = None
    slots: dict[str, Any] | None = None
    reason: str | None = None  # why a fallback happened (a code, never text)

    def as_state(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "source": self.source,
            "confidence": self.confidence,
            "slots": dict(self.slots or {}),
            "reason": self.reason,
        }


def fallback(reason: str, confidence: float | None = None) -> Route:
    return Route(FALLBACK_INTENT, "fallback", confidence, {}, reason)


def clean_slots(intent: str, slots: RouteSlots | dict[str, Any]) -> dict[str, Any] | None:
    """The slots that belong to `intent`, checked, or None when they cannot be used.

    A slot that does not belong to the intent is dropped; a missing one that the pipeline needs
    makes the decision unusable (the caller falls back to `general_chat`)."""
    raw = slots.model_dump() if isinstance(slots, RouteSlots) else dict(slots)

    def text(key: str) -> str | None:
        value = raw.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    match intent:
        case "general_chat":
            return {}
        case "manage_memory":
            action = raw.get("memory_action")
            if action == "list":
                return {"memory_action": "list"}
            content = text("memory_content")
            if action in ("save", "forget") and content and len(content) <= MAX_MEMORY_CHARS:
                return {"memory_action": action, "memory_content": content}
            return None
        case "generate_document":
            source = raw.get("source")
            if source not in ("thread", "last_answer", "topic"):
                return None
            if source != "topic":
                return {"source": source}
            topic = text("topic")
            return {"source": source, "topic": topic[:MAX_TOPIC_CHARS]} if topic else None
        case "deep_research":
            topic = text("topic")
            if topic is None:
                return None
            output = raw.get("output") if raw.get("output") in ("chat", "pdf") else "chat"
            return {"topic": topic[:MAX_TOPIC_CHARS], "output": output}
    return None


def user_messages(
    messages: Sequence[AnyMessage], *, count: int = HISTORY_USER_MESSAGES
) -> list[str]:
    """The last `count` user messages, oldest first, each cut short. Only human messages: the
    assistant's text, tool results and documents never reach the router."""
    texts = [m.text for m in messages if isinstance(m, HumanMessage)]
    return [text[:MESSAGE_CHARS] for text in texts[-count:]]


def render_input(prompt: Prompt, texts: Sequence[str], previous_intent: str | None) -> str:
    body = "\n".join(f"<message>{escape(text, quote=False)}</message>" for text in texts)
    previous = previous_intent if previous_intent in INTENTS else NO_INTENT
    return prompt.render(previous_intent=previous, user_messages=body)


def min_confidence(prompt: Prompt) -> float | None:
    """The threshold from the prompt's front-matter; None means "no threshold" (the router then
    falls back only on error, timeout or invalid output: doc 08 section 2.2)."""
    value = prompt.meta.get("min_confidence")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


async def classify(
    model: Runnable[Any, Any],
    texts: Sequence[str],
    previous_intent: str | None,
    *,
    config: RunnableConfig | None = None,
    prompt: Prompt | None = None,
    timeout_s: float = ROUTER_TIMEOUT_S,
) -> RouteDecision:
    """One router call. Raises on a timeout, a provider error or output that is not a valid
    decision."""
    prompt = prompt or load_prompt(PROMPT_NAME)
    request = render_input(prompt, texts, previous_intent)
    # The factory returns a Runnable; for this role it is a chat model (no fallback is configured).
    structured = cast("Any", model).with_structured_output(
        RouteDecision, method="json_schema", strict=True
    )
    raw = await asyncio.wait_for(structured.ainvoke(request, config), timeout=timeout_s)
    try:
        return raw if isinstance(raw, RouteDecision) else RouteDecision.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("the router's decision was not valid") from exc  # no text


def decide(
    decision: RouteDecision,
    *,
    threshold: float | None,
    pipelines: frozenset[str],
    disabled: frozenset[str],
) -> Route:
    """The route for a model decision: it, or the fallback and why."""
    confidence = decision.confidence
    if not 0.0 <= confidence <= 1.0:
        return fallback("invalid_confidence")
    if threshold is not None and confidence < threshold:
        return fallback("low_confidence", confidence)
    if decision.intent not in pipelines:
        return fallback("intent_unavailable", confidence)
    if decision.intent in disabled:
        return fallback("intent_disabled", confidence)
    slots = clean_slots(decision.intent, decision.slots)
    if slots is None:
        return fallback("invalid_slots", confidence)
    if decision.intent == FALLBACK_INTENT:
        return Route(FALLBACK_INTENT, "router", confidence, {})
    return Route(decision.intent, "router", confidence, slots)


def command_route(command: dict[str, Any], *, pipelines: frozenset[str]) -> Route:
    """The route for a parsed slash command (trusted: it comes from the run config)."""
    intent = command.get("intent")
    if intent not in INTENTS or intent not in pipelines:
        return fallback("intent_unavailable")
    slots = clean_slots(str(intent), command.get("slots") or {})
    if slots is None:
        return fallback("invalid_slots")
    return Route(cast("Intent", intent), "command", None, slots)


def _config_names(config: RunnableConfig, key: str) -> frozenset[str]:
    raw = config.get("configurable", {}).get(key) or ()
    return frozenset(str(name) for name in raw)


def make_router_node(
    model: Runnable[Any, Any] | None,
    *,
    pipelines: frozenset[str] = PIPELINES,
    timeout_s: float = ROUTER_TIMEOUT_S,
) -> ChatNode:
    """The node. Without a model (graphs built for tests and tools that never route) it still
    honours a slash command and otherwise stays silent: no route, no event, `general_chat`."""

    async def router(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        configurable = config.get("configurable", {})
        command = configurable.get("command")
        route: Route | None
        if isinstance(command, dict):
            route = command_route(command, pipelines=pipelines)
        elif model is None:
            return {}
        elif configurable.get("router_disabled"):
            route = fallback("router_disabled")
        else:
            route = await _ask(model, state, config, pipelines, timeout_s)
        get_stream_writer()(
            {
                "kind": "route",
                "intent": route.intent,
                "source": route.source,
                "confidence": route.confidence,
            }
        )
        return {"route": route.as_state()}

    return router


async def _ask(
    model: Runnable[Any, Any],
    state: ChatState,
    config: RunnableConfig,
    pipelines: frozenset[str],
    timeout_s: float,
) -> Route:
    texts = user_messages(state["messages"])
    if not texts:
        return fallback("no_user_message")
    previous = (state.get("route") or {}).get("intent")
    prompt = load_prompt(PROMPT_NAME)
    try:
        decision = await classify(
            model, texts, previous, config=config, prompt=prompt, timeout_s=timeout_s
        )
    except TimeoutError:
        _log.warning("router_fallback", reason="timeout")
        return fallback("timeout")
    except Exception as exc:  # any failure falls back; the log carries the type only
        _log.warning("router_fallback", reason="error", error_type=type(exc).__name__)
        return fallback("error")
    route = decide(
        decision,
        threshold=min_confidence(prompt),
        pipelines=pipelines,
        disabled=_config_names(config, "disabled_intents"),
    )
    if route.source == "fallback":
        _log.info("router_fallback", reason=route.reason)
    return route
