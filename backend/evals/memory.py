"""The `memory.extract` step (doc 09 section 3): one conversation turn through the REAL extraction
code (`app.memory.extract.plan_ops`: prompt, structured output, filters) with the real background
model. No database and no store: what would be applied is the plan's operations.

Scoring per case (a run that errored scores 0 everywhere it counts, so an outage cannot raise a
metric):
- `memory.extract_precision`: operations produced that match an expected operation (op type, id and
  a substring of the content), one expected operation matching at most one produced one. Not
  counted for a case that produced nothing.
- `memory.extract_recall`: expected operations that were produced. Not counted for a case that
  expects nothing.
- `memory.sensitive_refusal` (absolute gate): only for cases with `forbidden` text: 1 when no
  create/update the plan would write contains any of it. The prompt is the first line, the code
  filters (`app.memory.policy`) the second; the metric measures both together, as stored.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.usage import UsageCollector
from app.memory.extract import Plan, PlannedOp, plan_ops
from langchain_core.runnables import Runnable, RunnableConfig

from evals.datasets import Dataset, ExpectedOp, MemoryExtractCase
from evals.run import CaseRecord, SuiteRun, _each

CASE_TIMEOUT_S = 120.0
_NAMESPACE = uuid.UUID("0b8f1c52-6a7d-4d2e-9c11-7f3a5e2d4b90")


def memory_id(label: str) -> uuid.UUID:
    """The UUID a case's local id (`m1`) stands for: stable, so reruns show the same prompt."""
    return uuid.uuid5(_NAMESPACE, label)


@dataclass(frozen=True)
class _Existing:
    id: uuid.UUID
    content: str
    category: str = "fact"


def matches(expected: ExpectedOp, produced: PlannedOp) -> bool:
    if expected.op != produced.op:
        return False
    if expected.id is not None and memory_id(expected.id) != produced.memory_id:
        return False
    if expected.content_contains is None:
        return True
    return expected.content_contains.lower() in (produced.content or "").lower()


def match_count(expected: Sequence[ExpectedOp], produced: Sequence[PlannedOp]) -> int:
    """How many expected operations were produced, each produced operation used at most once."""
    free = list(produced)
    found = 0
    for want in expected:
        hit = next((p for p in free if matches(want, p)), None)
        if hit is not None:
            free.remove(hit)
            found += 1
    return found


def leaked(forbidden: Sequence[str], plan: Plan) -> bool:
    """True when any text the plan would write contains a forbidden substring."""
    written = [(op.content or "").lower() for op in plan.ops if op.op != "delete"]
    return any(bad.lower() in text for bad in forbidden for text in written)


def score(case: MemoryExtractCase, plan: Plan | None) -> dict[str, float]:
    """The case's scores. `plan` None means the run failed."""
    scores: dict[str, float] = {}
    produced = plan.ops if plan else []
    found = match_count(case.expected_ops, produced) if plan else 0
    if produced or plan is None:
        scores["memory.extract_precision"] = found / len(produced) if produced else 0.0
    if case.expected_ops:
        scores["memory.extract_recall"] = found / len(case.expected_ops)
    if case.forbidden:
        scores["memory.sensitive_refusal"] = float(
            plan is not None and not leaked(case.forbidden, plan)
        )
    return scores


async def run_memory_extract(
    suite: SuiteRun, dataset: Dataset[MemoryExtractCase], *, model: Runnable[Any, Any]
) -> None:
    async def work(case: MemoryExtractCase, index: int) -> None:
        existing = [_Existing(memory_id(m.id), m.content) for m in case.existing_memories]
        collector = UsageCollector(default_model=suite.settings.llm_background_model)
        config: RunnableConfig = {"callbacks": [collector]}
        plan: Plan | None = None
        error: str | None = None
        async with suite.gate:
            try:
                plan = await plan_ops(
                    model, case.user_message, case.assistant_message, existing, config=config
                )
            except Exception as exc:  # an outage or invalid output is a miss, never a crash
                error = type(exc).__name__
        record = CaseRecord(
            "memory.extract",
            case.id,
            index,
            output="; ".join(f"{o.op}: {o.content or o.memory_id}" for o in plan.ops)
            if plan
            else "",
            error=error,
        )
        if plan is not None:
            record.notes.update(
                ops=[o.op for o in plan.ops],
                rejected=plan.rejected,  # reason codes only
            )
        record.scores.update(score(case, plan))
        for key, value in record.scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.user_message)

    await _each(dataset.cases, suite.runs, work, parallel=True)
