from decimal import Decimal

import pytest
from app.llm.pricing import PRICES, Usage, cost_usd, usage_from_metadata


def test_terra_cost_splits_cached_and_uncached_input() -> None:
    # 10k input of which 8k cached, 1k output: 2k*$1/M + 8k*$0.05/M + 1k*$5/M
    usage = Usage(prompt_tokens=10_000, cached_tokens=8_000, completion_tokens=1_000)
    assert cost_usd("gpt-6.1-terra", usage) == Decimal("0.0074")


def test_luna_cost() -> None:
    usage = Usage(prompt_tokens=1_000_000, cached_tokens=0, completion_tokens=1_000_000)
    assert cost_usd("gpt-6-luna", usage) == Decimal("0.60")


def test_dated_snapshot_matches_its_family() -> None:
    usage = Usage(prompt_tokens=1_000_000, cached_tokens=0, completion_tokens=0)
    assert cost_usd("gpt-5.4-mini-2026-03-17", usage) == Decimal("0.75")
    assert cost_usd("gpt-5.5-2026-04-23", usage) == Decimal("5")


def test_unknown_model_is_priced_at_the_most_expensive_known_rate() -> None:
    usage = Usage(prompt_tokens=1_000_000, cached_tokens=0, completion_tokens=1_000_000)
    most_expensive = max(cost_usd(model, usage) for model in PRICES)
    assert cost_usd("gpt-9-mystery", usage) == most_expensive


def test_cached_tokens_are_clamped_to_prompt_tokens() -> None:
    usage = Usage(prompt_tokens=100, cached_tokens=500, completion_tokens=0)
    assert cost_usd("gpt-6.1-terra", usage) == Decimal("0.000005")


def test_zero_usage_costs_nothing() -> None:
    assert cost_usd("gpt-6.1-terra", Usage(0, 0, 0)) == Decimal(0)


def test_usage_from_langchain_metadata_reads_cache_and_reasoning() -> None:
    meta = {
        "input_tokens": 1200,
        "output_tokens": 300,
        "total_tokens": 1500,
        "input_token_details": {"cache_read": 1000},
        "output_token_details": {"reasoning": 200},
    }
    usage = usage_from_metadata(meta)
    assert usage == Usage(prompt_tokens=1200, cached_tokens=1000, completion_tokens=300)


@pytest.mark.parametrize("meta", [None, {}])
def test_usage_from_missing_metadata_is_zero(meta: dict[str, object] | None) -> None:
    assert usage_from_metadata(meta) == Usage(0, 0, 0)
