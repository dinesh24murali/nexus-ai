"""Model price table and cost accounting (docs/02_outputs/08 section 10).

Prices are USD per 1M tokens. `completion_tokens` is OpenAI's `output_tokens`, which already
includes reasoning tokens, so reasoning is billed as output with no extra term. Re-check the
table whenever models change (memory/ai.md lists the date it was last verified).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

_log = structlog.get_logger("nexus.llm.pricing")

_MILLION = Decimal(1_000_000)
_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Price:
    input: Decimal
    cached_input: Decimal
    output: Decimal


def _p(input_: str, cached: str, output: str) -> Price:
    return Price(Decimal(input_), Decimal(cached), Decimal(output))


# Checked against OpenAI docs on 2026-11-19 (memory/ai.md).
PRICES: dict[str, Price] = {
    "gpt-6.1-terra": _p("1", "0.05", "5"),
    "gpt-6-luna": _p("0.10", "0.01", "0.50"),
    "gpt-6.2-sol": _p("2", "0.10", "10"),
    "gpt-5.4-mini": _p("0.75", "0.075", "4.50"),
    "gpt-5.5": _p("5", "0.50", "30"),
}


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int  # includes reasoning tokens


def _most_expensive() -> Price:
    return max(PRICES.values(), key=lambda p: (p.output, p.input, p.cached_input))


def price_for(model: str) -> Price:
    """Exact name, or a dated snapshot of a known model. Unknown models get the highest known
    rates: a budget must never under-count spend because a price row is missing."""
    base = _SNAPSHOT_SUFFIX.sub("", model)
    for candidate in (model, base):
        if candidate in PRICES:
            return PRICES[candidate]
    _log.warning("unknown_model_price", model=model)
    return _most_expensive()


def cost_usd(model: str, usage: Usage) -> Decimal:
    price = price_for(model)
    cached = min(max(usage.cached_tokens, 0), usage.prompt_tokens)
    uncached = max(usage.prompt_tokens - cached, 0)
    total = (
        uncached * price.input
        + cached * price.cached_input
        + max(usage.completion_tokens, 0) * price.output
    )
    return total / _MILLION


def usage_from_metadata(meta: Mapping[str, Any] | None) -> Usage:
    """Read LangChain `AIMessage.usage_metadata` (None or empty means no usage reported)."""
    if not meta:
        return Usage(0, 0, 0)
    details = meta.get("input_token_details") or {}
    return Usage(
        prompt_tokens=int(meta.get("input_tokens", 0)),
        cached_tokens=int(details.get("cache_read", 0)),
        completion_tokens=int(meta.get("output_tokens", 0)),
    )
