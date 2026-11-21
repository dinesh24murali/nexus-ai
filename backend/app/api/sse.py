"""Server-Sent Events: typed event payloads and framing (ADR-0006, docs/02_outputs/08 section 8).

The event models mirror the `*Event` components of `docs/02_outputs/04_api_spec.yaml` (the
contract); `tests/api/test_sse.py` validates every one of them against the spec itself. A frame is
`event: <type>`, `id: <seq>`, `data: <json>` and a blank line; JSON never contains a raw newline,
so one `data:` line is always enough.
"""

import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer

PING = ": ping\n\n"  # a comment frame: keeps proxies from closing an idle stream
PING_INTERVAL_S = 15.0


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True)


class RunStartedEvent(_Event):
    type: Literal["run_started"] = "run_started"
    run_id: uuid.UUID
    user_message_id: uuid.UUID | None  # null for regenerate and resume
    assistant_message_id: uuid.UUID


class TokenEvent(_Event):
    type: Literal["token"] = "token"
    delta: str


class Usage(BaseModel):
    model: str
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int  # includes reasoning tokens
    cost_usd: Decimal

    @field_serializer("cost_usd")
    def _as_number(self, value: Decimal) -> float:
        return float(value)


class UsageEvent(_Event):
    type: Literal["usage"] = "usage"
    usage: Usage


class DoneEvent(_Event):
    type: Literal["done"] = "done"
    message_id: uuid.UUID
    finish_reason: Literal["stop", "length", "cancelled", "tool_limit"]


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: Literal[
        "MODERATION_FLAGGED", "BUDGET_EXCEEDED", "LLM_UNAVAILABLE", "CONTEXT_TOO_LONG", "INTERNAL"
    ]
    message: str
    retryable: bool


Event = RunStartedEvent | TokenEvent | UsageEvent | DoneEvent | ErrorEvent

TERMINAL_TYPES = frozenset({"done", "error"})  # the stream ends after one of these


def frame(event: Event, seq: int) -> str:
    return f"event: {event.type}\nid: {seq}\ndata: {event.model_dump_json()}\n\n"
