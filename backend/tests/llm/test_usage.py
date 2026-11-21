from decimal import Decimal

from app.llm.pricing import Usage
from app.llm.usage import UsageCollector
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult


def result(
    meta: dict[str, object] | None, *, model_name: str | None = None, text: str = "x"
) -> LLMResult:
    message = AIMessage(
        content=text,
        usage_metadata=meta,
        response_metadata={"model_name": model_name} if model_name else {},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def meta(i: int, o: int, cached: int = 0) -> dict[str, object]:
    return {
        "input_tokens": i,
        "output_tokens": o,
        "total_tokens": i + o,
        "input_token_details": {"cache_read": cached},
    }


def test_sums_every_call_of_a_run_per_model() -> None:
    collector = UsageCollector(default_model="gpt-6-luna")

    collector.on_llm_end(result(meta(1000, 100, 400), model_name="gpt-6.1-terra"))
    collector.on_llm_end(result(meta(2000, 50), model_name="gpt-6.1-terra"))
    collector.on_llm_end(result(meta(500, 20), model_name="gpt-6-luna"))

    assert collector.by_model["gpt-6.1-terra"] == Usage(3000, 400, 150)
    assert collector.by_model["gpt-6-luna"] == Usage(500, 0, 20)
    assert collector.total == Usage(3500, 400, 170)
    assert collector.calls == 3


def test_a_call_without_a_reported_model_is_billed_to_the_default_model() -> None:
    collector = UsageCollector(default_model="gpt-6-luna")

    collector.on_llm_end(result(meta(10, 5)))

    assert collector.by_model == {"gpt-6-luna": Usage(10, 0, 5)}


def test_calls_without_usage_are_ignored() -> None:
    collector = UsageCollector(default_model="m")

    collector.on_llm_end(result(None))

    assert collector.by_model == {}
    assert collector.total == Usage(0, 0, 0)
    assert collector.cost_usd == Decimal(0)


def test_cost_is_priced_per_model_with_the_cached_rate() -> None:
    collector = UsageCollector(default_model="gpt-6-luna")
    collector.on_llm_end(result(meta(1_000_000, 100_000, 400_000), model_name="gpt-6.1-terra"))

    # terra: 600k uncached x $1 + 400k cached x $0.05 + 100k output x $5, per million
    assert collector.cost_usd == Decimal("0.6") + Decimal("0.02") + Decimal("0.5")


def test_the_main_model_is_the_one_that_produced_the_most_output() -> None:
    collector = UsageCollector(default_model="gpt-6-luna")
    collector.on_llm_end(result(meta(900, 30), model_name="gpt-6-luna"))  # summariser
    collector.on_llm_end(result(meta(100, 400), model_name="gpt-6.1-terra"))  # answer

    assert collector.main_model == "gpt-6.1-terra"


def test_embedding_spend_adds_to_the_cost_but_not_to_the_token_totals() -> None:
    collector = UsageCollector(default_model="gpt-6.1-terra")
    collector.add_embedding("text-embedding-3-small", 5_000_000)  # $0.02 per million input tokens

    assert collector.cost_usd == Decimal("0.10")
    assert collector.total.prompt_tokens == 0
