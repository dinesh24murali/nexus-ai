"""The `router.intents` step (doc 09 section 3, TASK-038): the router alone, one call per case.

No pipeline runs and no tool is involved, so the step costs one `gpt-6-luna` call per case. Every
intent counts as built (the router is measured, not the build), no intent is switched off, and the
confidence threshold is the one in the front-matter of `nexus.route`, so the numbers are what a
user would get.

Metrics (a case whose call failed counts as a miss: an outage cannot raise a metric):
- `router.intent_accuracy`: the routed intent is `expected_intent` or one of `acceptable_intents`.
- `router.macro_f1`: macro-F1 over the four intents on the same predictions (the confusion matrix
  is in the run report).
- `router.slot_accuracy`: of the correctly routed cases that name slots, the share whose slots all
  contain the expected text.
- `router.fallback_rate` (lower): share of cases routed to `general_chat` because the confidence was
  under `min_confidence`, not because that was the prediction. The report also shows the mean
  confidence of right and wrong routes, which decides whether the threshold is kept (doc 08 2.2).
- `router.latency_p95_ms` (lower): the router call, per case.
"""

import time
from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.graph.intents import INTENTS
from app.graph.router import (
    Route,
    classify,
    decide,
    fallback,
    min_confidence,
    user_messages,
)
from app.llm.prompts import load_prompt
from app.llm.usage import UsageCollector
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig

from evals.datasets import Dataset, RouterCase
from evals.run import CaseRecord, SuiteRun, _each

ALL_INTENTS = frozenset(INTENTS)


def history_messages(case: RouterCase) -> list[AnyMessage]:
    """The case's earlier turns plus the message to route, as the graph would hold them."""
    turns: list[AnyMessage] = [
        HumanMessage(t.content) if t.role == "user" else AIMessage(content=t.content)
        for t in case.history
    ]
    return [*turns, HumanMessage(case.input)]


def previous_intent(case: RouterCase) -> str | None:
    """The intent of the last answered turn in the case's history, if it says."""
    return next((t.intent for t in reversed(case.history) if t.intent), None)


def slots_match(case: RouterCase, route: Route) -> bool:
    slots = route.slots or {}
    return all(
        needle.casefold() in str(slots.get(key) or "").casefold()
        for key, needle in case.expected_slots_contains.items()
    )


def macro_f1(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Macro-F1 over the four intents for (expected, predicted) pairs. An intent that is neither
    expected nor predicted anywhere is left out of the average (nothing to measure)."""
    scores: list[float] = []
    for intent in INTENTS:
        tp = sum(1 for e, p in pairs if e == intent and p == intent)
        fp = sum(1 for e, p in pairs if e != intent and p == intent)
        fn = sum(1 for e, p in pairs if e == intent and p != intent)
        if tp + fp + fn == 0:
            continue
        scores.append(2 * tp / (2 * tp + fp + fn))
    return sum(scores) / len(scores) if scores else None


def _confusion(pairs: Sequence[tuple[str, str]]) -> dict[str, dict[str, int]]:
    counts = Counter(pairs)
    return {e: {p: counts[(e, p)] for p in INTENTS if counts[(e, p)]} for e in INTENTS}


async def run_router_intents(
    suite: SuiteRun, dataset: Dataset[RouterCase], *, model: Runnable[Any, Any]
) -> None:
    prompt = load_prompt("nexus.route")
    threshold = min_confidence(prompt)
    pairs: list[tuple[str, str]] = []
    confidence_right: list[float] = []
    confidence_wrong: list[float] = []

    async def work(case: RouterCase, index: int) -> None:
        collector = UsageCollector(default_model=suite.settings.llm_router_model)
        config: RunnableConfig = {"callbacks": [collector]}
        texts = user_messages(history_messages(case))
        route: Route | None = None
        error: str | None = None
        latency_ms = 0.0
        async with suite.gate:
            started = time.perf_counter()
            try:
                decision = await classify(
                    model, texts, previous_intent(case), config=config, prompt=prompt
                )
                latency_ms = (time.perf_counter() - started) * 1000
                route = decide(
                    decision, threshold=threshold, pipelines=ALL_INTENTS, disabled=frozenset()
                )
            except Exception as exc:  # a timeout, an outage or invalid output: a miss
                error = type(exc).__name__
                route = fallback("error")
        record = CaseRecord(
            "router.intents", case.id, index, output=route.intent if route else "", error=error
        )
        correct = error is None and route.intent in case.right_intents
        record.scores["router.intent_accuracy"] = float(correct)
        if case.expected_slots_contains and correct:
            record.scores["router.slot_accuracy"] = float(slots_match(case, route))
        record.scores["router.fallback_rate"] = float(
            error is None and route.reason == "low_confidence"
        )
        if error is None:
            record.scores["router.latency_p95_ms"] = latency_ms
            if route.confidence is not None:
                (confidence_right if correct else confidence_wrong).append(route.confidence)
        record.notes.update(
            expected=case.expected_intent,
            predicted=route.intent,
            confidence=route.confidence,
            reason=route.reason,
            slots=route.slots,
            latency_ms=round(latency_ms, 1),
        )
        # An errored call is a wrong prediction for the confusion matrix, never an excluded one.
        pairs.append((route.intent if correct else case.expected_intent, route.intent))
        for key, value in record.scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.input)

    await _each(dataset.cases, suite.runs, work, parallel=True)

    if (f1 := macro_f1(pairs)) is not None:
        suite.scores.add("router.macro_f1", f1)
    suite.extras["router"] = {
        "min_confidence": threshold,
        "prompt_version": prompt.version,
        "confusion": _confusion(pairs),
        "mean_confidence_right": _mean(confidence_right),
        "mean_confidence_wrong": _mean(confidence_wrong),
    }


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None
