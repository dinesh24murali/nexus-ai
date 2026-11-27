"""The `rag.golden` step (doc 09 section 3): questions over the fixture corpus, through the real
graph with the real retriever, scored on retrieval, faithfulness, correctness and citations.

Scoring per answerable case:
- `rag.context_recall`: reference (file, page) pairs among the passages the tool returned. A turn
  where the model never searched scores 0.
- `rag.search_rate`: did the model search at all (informational: it separates "did not look" from
  "looked and missed").
- `rag.citation_validity`: `[n]` markers that point at a returned passage; an answer with no marker
  scores 0.
- `rag.faithfulness` (judge, only when something was returned): every claim supported by the tool
  results the model saw.
- `rag.answer_correctness` (judge): the case's key facts are in the answer; the same score is also
  recorded as `rag.answer_correctness.whole_doc` for whole-document questions, the evidence for the
  per-file synopsis decision.
Unanswerable cases only get `rag.unanswerable_accuracy` (judge: the answer says it is not in the
files). A run that errored counts as a miss, so an outage cannot raise a metric.
"""

import uuid
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from evals.datasets import Dataset, RagCase
from evals.metrics import citation_validity, context_recall
from evals.run import CaseRecord, SuiteRun, _each, _turn_costs


async def run_rag_golden(
    suite: SuiteRun,
    dataset: Dataset[RagCase],
    *,
    graph: CompiledStateGraph[Any, Any, Any, Any],
    user_id: uuid.UUID,
) -> None:
    async def work(case: RagCase, index: int) -> None:
        result = await suite.chat(case.input, graph=graph, user_id=str(user_id))
        retrieved = [(str(c["title"]), c.get("page")) for c in result.citations]
        record = CaseRecord("rag.golden", case.id, index, result.output, error=result.error)
        record.notes.update(
            kind=case.kind,
            searched=bool(result.tool_results),
            retrieved=[[name, page] for name, page in retrieved],
        )
        _turn_costs(suite, result)
        scores = record.scores

        if not case.answerable:
            if result.error is not None:
                scores["rag.unanswerable_accuracy"] = 0.0
            elif suite.judges is not None:
                said = await suite.judges.not_found(case.input, result.output)
                if said is not None:
                    scores["rag.unanswerable_accuracy"] = float(said)
        else:
            refs = [(r.file, r.page) for r in case.reference_chunks]
            scores["rag.context_recall"] = context_recall(refs, retrieved)
            scores["rag.search_rate"] = float(bool(result.tool_results))
            if result.error is None:
                scores["rag.citation_validity"] = citation_validity(
                    result.output, [int(c["index"]) for c in result.citations]
                )
                if suite.judges is not None:
                    if result.tool_results:
                        faithful = await suite.judges.faithfulness(
                            case.input, result.tool_results, result.output
                        )
                        if faithful is not None:
                            scores["rag.faithfulness"] = faithful
                    kept = await suite.judges.fact_retention(
                        result.output, case.key_facts or [case.reference_answer]
                    )
                    if kept is not None:
                        scores["rag.answer_correctness"] = kept
                        if case.kind == "whole_doc":
                            scores["rag.answer_correctness.whole_doc"] = kept
        for key, value in scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.input)

    await _each(dataset.cases, suite.runs, work, parallel=True)
