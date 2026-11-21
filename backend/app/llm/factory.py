"""The only place model clients are created (ADR-0005; enforced by a ruff banned-api rule).

`get_model(role)` applies the per-role policy from docs/02_outputs/08 section 3: model and
reasoning effort from settings, max output (reasoning included), timeout, bounded retries, an
optional fallback model, the Responses API with `store=False`, and no `temperature` unless the
effort is `none` (GPT-6 rejects it otherwise).

Tracing callbacks are not attached here: they travel in the per-run config
(`llm/tracing.py`) so one handler covers the whole graph run.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.core.config import ReasoningEffort, Settings, get_settings
from app.llm.fake import FakeChatModel

Role = Literal["chat", "summarize", "title", "memory_extract", "judge", "router", "long_form"]
ROLES: tuple[str, ...] = get_args(Role)

# Errors worth trying the fallback model for: the primary is slow or down. Client errors (4xx) are
# our bug, and a rate limit that survived the SDK's retries would only repeat on the fallback.
_FALLBACK_ON = (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)

_ENCRYPTED_REASONING = "reasoning.encrypted_content"


class LLMConfigError(Exception):
    pass


@dataclass(frozen=True)
class _Spec:
    model: str
    effort: ReasoningEffort
    max_output_tokens: int
    timeout_s: float
    retries: int
    temperature: float | None = None  # honoured only when effort is "none"
    fallback_model: str | None = None  # always run at effort "none"


def _spec(role: str, s: Settings) -> _Spec:
    retries = s.llm_max_retries
    match role:
        case "chat":
            return _Spec(
                s.llm_chat_model,
                s.llm_chat_reasoning_effort,
                4096,
                s.llm_request_timeout_s,
                retries,
                fallback_model=s.llm_fallback_model,
            )
        case "summarize":
            return _Spec(s.llm_background_model, "none", 800, 30, retries)
        case "title":
            return _Spec(s.llm_background_model, "none", 20, 15, min(retries, 1), temperature=0.2)
        case "memory_extract":
            return _Spec(s.llm_background_model, "none", 600, 30, retries)
        case "judge":
            return _Spec(s.llm_judge_model, "medium", 2000, 90, retries)
        case "router":
            # Any failure routes to general_chat, so a retry or a fallback only adds latency.
            return _Spec(s.llm_router_model, "none", 150, 5, 0)
        case "long_form":
            return _Spec(
                s.llm_long_form_model,
                s.llm_long_form_reasoning_effort,
                4000,
                120,
                retries,
                fallback_model=s.llm_fallback_model,
            )
    raise LLMConfigError(f"unknown model role: {role!r}")


def _client(
    s: Settings,
    *,
    model: str,
    effort: ReasoningEffort,
    max_output_tokens: int,
    timeout_s: float,
    retries: int,
    temperature: float | None,
) -> ChatOpenAI:
    options: dict[str, Any] = {}
    if effort == "none":
        if temperature is not None:
            options["temperature"] = temperature
    else:
        # With store=False the server keeps no reasoning items; the client must carry them
        # between the calls of a tool loop, and only the encrypted form can be replayed.
        options["include"] = [_ENCRYPTED_REASONING]
    return ChatOpenAI(
        model=model,
        api_key=s.openai_api_key,
        use_responses_api=True,
        store=False,
        reasoning={"effort": effort},
        max_tokens=max_output_tokens,
        timeout=timeout_s,
        max_retries=retries,
        **options,
    )


def get_model(
    role: Role,
    *,
    tools: Sequence[Any] | None = None,
    settings: Settings | None = None,
    model_override: str | None = None,
) -> Runnable[Any, Any]:
    """Build the chat model for `role`, with `tools` bound to the primary and its fallback.

    `model_override` replaces the primary model name (the `CHAT_MODEL_OVERRIDE` kill switch is
    read by the caller from runtime flags; this function stays synchronous and pure).
    """
    s = settings or get_settings()
    spec = _spec(role, s)

    if s.llm_mode == "fake":
        fake: BaseChatModel = FakeChatModel(role=role)
        return fake.bind_tools(tools) if tools else fake

    if not s.openai_api_key.get_secret_value():
        raise LLMConfigError("OPENAI_API_KEY is not set (use LLM_MODE=fake to work offline)")

    def build(model: str, effort: ReasoningEffort) -> Runnable[Any, Any]:
        client = _client(
            s,
            model=model,
            effort=effort,
            max_output_tokens=spec.max_output_tokens,
            timeout_s=spec.timeout_s,
            retries=spec.retries,
            temperature=spec.temperature,
        )
        return client.bind_tools(tools) if tools else client

    primary = build(model_override or spec.model, spec.effort)
    if not spec.fallback_model:
        return primary
    return primary.with_fallbacks(
        [build(spec.fallback_model, "none")], exceptions_to_handle=_FALLBACK_ON
    )
