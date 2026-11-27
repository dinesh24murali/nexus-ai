"""The `rag.golden` step: scripted model, stub retriever, stub judges. No network."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.graph.builder import build_graph
from app.llm.fake import FakeChatModel
from evals.datasets import ChunkRef, Dataset, RagCase
from evals.judges import Judges
from evals.langfuse_log import EvalLogger
from evals.rag import run_rag_golden
from evals.run import SuiteRun
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from tests.graph.retrieval_support import StubRetriever, passage
from tests.services.support import ExplodingFake

Q3 = "q3-business-review.pdf"
FIXTURES = Path(__file__).parents[2] / "app" / "llm" / "fixtures"


def judges(*, not_found: bool = True, faithful: bool = True, facts_present: bool = True) -> Judges:
    def reply(messages: Any) -> AIMessage:
        prompt = messages[0].content
        if "<passages>" in prompt:
            return AIMessage(json.dumps({"claims": [{"claim": "c", "supported": faithful}]}))
        if "<facts>" in prompt:
            n = len(
                [
                    line
                    for line in prompt.split("<facts>")[1].split("</facts>")[0].splitlines()
                    if line.strip()
                ]
            )
            return AIMessage(json.dumps({"facts": [{"fact": "f", "present": facts_present}] * n}))
        return AIMessage(json.dumps({"not_found": not_found}))

    return Judges(RunnableLambda(reply))


def case(
    id_: str, text: str, kind: str, *, page: int | None = 2, answerable: bool = True
) -> RagCase:
    return RagCase(
        id=id_,
        input=text,
        kind=kind,
        reference_answer="reference",
        reference_chunks=[ChunkRef(file=Q3, page=page)] if answerable else [],
        evidence=["x"] if answerable else [],
        key_facts=["a fact"] if answerable else [],
        answerable=answerable,
    )


def make_suite(judge: Judges | None) -> SuiteRun:
    settings = Settings(_env_file=None, app_env="test", llm_mode="fake")
    return SuiteRun(
        settings=settings,
        graph=None,  # type: ignore[arg-type]
        summarizer=None,
        judges=judge,
        logger=EvalLogger(None, run_name="t", environment="test"),
        runs=1,
    )


def rag_graph(retriever: StubRetriever) -> Any:
    return build_graph(
        checkpointer=None,
        chat_model=FakeChatModel(fixtures_dir=FIXTURES),
        summarize_model=FakeChatModel(role="summarize"),
        retriever=retriever,
    )


async def run(
    cases: list[RagCase], judge: Judges | None, retriever: StubRetriever | None = None
) -> SuiteRun:
    suite = make_suite(judge)
    retriever = retriever or StubRetriever([passage(filename=Q3, page=2)])
    await run_rag_golden(
        suite, Dataset("rag.golden", "v", cases), graph=rag_graph(retriever), user_id=uuid.uuid4()
    )
    return suite


def metric(suite: SuiteRun, key: str) -> float | None:
    return suite.scores.mean(key)


async def test_a_question_answered_from_the_right_page_scores_on_every_metric() -> None:
    suite = await run([case("rag-001", "[[fixture:rag]] churn?", "fact")], judges())

    assert metric(suite, "rag.context_recall") == 1.0
    assert metric(suite, "rag.citation_validity") == 1.0
    assert metric(suite, "rag.search_rate") == 1.0
    assert metric(suite, "rag.faithfulness") == 1.0
    assert metric(suite, "rag.answer_correctness") == 1.0
    assert metric(suite, "rag.answer_correctness.whole_doc") is None  # no such case ran


async def test_skipping_the_search_is_a_retrieval_miss_and_leaves_faithfulness_unmeasured() -> None:
    suite = await run([case("rag-030", "summarise the document", "whole_doc", page=None)], judges())

    assert metric(suite, "rag.context_recall") == 0.0
    assert metric(suite, "rag.search_rate") == 0.0
    assert metric(suite, "rag.citation_validity") == 0.0
    assert metric(suite, "rag.faithfulness") is None  # there were no passages to be faithful to
    assert metric(suite, "rag.answer_correctness.whole_doc") == 1.0  # still judged on the answer


async def test_the_wrong_page_is_not_recall() -> None:
    retriever = StubRetriever([passage(filename=Q3, page=1)])
    suite = await run(
        [case("rag-001", "[[fixture:rag]] churn?", "fact", page=2)], judges(), retriever
    )

    assert metric(suite, "rag.context_recall") == 0.0
    assert metric(suite, "rag.search_rate") == 1.0


async def test_an_unanswerable_question_is_scored_on_saying_not_found_only() -> None:
    answers = await run(
        [case("rag-034", "[[fixture:rag]] q2 revenue?", "unanswerable", answerable=False)],
        judges(not_found=True),
    )
    assert metric(answers, "rag.unanswerable_accuracy") == 1.0
    assert metric(answers, "rag.context_recall") is None
    assert metric(answers, "rag.answer_correctness") is None

    made_up = await run(
        [case("rag-034", "[[fixture:rag]] q2 revenue?", "unanswerable", answerable=False)],
        judges(not_found=False),
    )
    assert metric(made_up, "rag.unanswerable_accuracy") == 0.0


async def test_an_unfaithful_or_incorrect_answer_scores_zero() -> None:
    suite = await run(
        [case("rag-001", "[[fixture:rag]] churn?", "fact")],
        judges(faithful=False, facts_present=False),
    )

    assert metric(suite, "rag.faithfulness") == 0.0
    assert metric(suite, "rag.answer_correctness") == 0.0


async def test_without_a_judge_only_the_deterministic_metrics_are_measured() -> None:
    suite = await run([case("rag-001", "[[fixture:rag]] churn?", "fact")], None)

    assert metric(suite, "rag.context_recall") == 1.0
    assert metric(suite, "rag.faithfulness") is None
    assert metric(suite, "rag.answer_correctness") is None


async def test_each_case_is_recorded_with_its_answer_and_the_passages_it_saw() -> None:
    suite = await run([case("rag-001", "[[fixture:rag]] churn?", "fact")], judges())

    (record,) = suite.records
    assert (record.dataset, record.case_id, record.error) == ("rag.golden", "rag-001", None)
    assert record.output.endswith("[1].")
    assert record.notes["kind"] == "fact"
    assert record.notes["retrieved"] == [[Q3, 2]]


async def test_the_search_runs_as_the_corpus_user_not_the_default_eval_user() -> None:
    retriever = StubRetriever([passage(filename=Q3, page=2)])
    user = uuid.uuid4()
    suite = make_suite(None)
    await run_rag_golden(
        suite,
        Dataset("rag.golden", "v", [case("rag-001", "[[fixture:rag]] churn?", "fact")]),
        graph=rag_graph(retriever),
        user_id=user,
    )
    assert retriever.calls[0][0] == user


@pytest.mark.parametrize("bad", ["fact"])
def test_the_helper_builds_valid_cases(bad: str) -> None:
    assert case("rag-001", "q", bad).answerable


async def test_a_failed_run_counts_as_a_miss_for_every_kind_of_case() -> None:
    broken = build_graph(
        checkpointer=None,
        chat_model=ExplodingFake(role="chat", error=RuntimeError("down")),
        summarize_model=FakeChatModel(role="summarize"),
        retriever=StubRetriever(),
    )
    suite = make_suite(judges())
    cases = [
        case("rag-001", "q", "fact"),
        case("rag-034", "q", "unanswerable", answerable=False),
    ]
    await run_rag_golden(
        suite, Dataset("rag.golden", "v", cases), graph=broken, user_id=uuid.uuid4()
    )

    assert suite.errors == 2
    assert metric(suite, "rag.context_recall") == 0.0  # an outage cannot raise a metric
    assert metric(suite, "rag.unanswerable_accuracy") == 0.0
    assert metric(suite, "rag.faithfulness") is None
