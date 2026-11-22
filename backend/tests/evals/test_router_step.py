"""The `router.intents` step (TASK-038): scoring is deterministic and the dataset is sound. A
scripted router stands in for the real one (real calls belong in `make eval`)."""

import json
from collections import Counter
from typing import Any

import pytest
from app.core.config import Settings
from app.graph.router import Route
from evals.datasets import DATASETS_DIR, DatasetError, RouterCase, load_dataset
from evals.langfuse_log import EvalLogger
from evals.router import (
    history_messages,
    macro_f1,
    previous_intent,
    run_router_intents,
    slots_match,
)
from evals.run import SuiteRun, _metrics
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import ValidationError


def case(**fields: Any) -> RouterCase:
    base: dict[str, Any] = {"id": "ri-900", "input": "hello", "expected_intent": "general_chat"}
    return RouterCase.model_validate({**base, **fields})


def make_suite() -> SuiteRun:
    settings = Settings(_env_file=None, app_env="test", llm_mode="fake")
    return SuiteRun(
        settings=settings,
        graph=None,  # type: ignore[arg-type]
        summarizer=None,
        judges=None,
        logger=EvalLogger(None, run_name="t", environment="test"),
        runs=1,
    )


class Scripted:
    """A router that decides by the newest user message (a dict text -> decision) and records what
    it was shown."""

    def __init__(self, decisions: dict[str, dict[str, Any]], *, fail: Exception | None = None):
        self.decisions = decisions
        self.fail = fail
        self.prompts: list[str] = []

    def with_structured_output(self, schema: Any, **options: Any) -> Runnable[Any, Any]:
        def respond(prompt: str) -> Any:
            self.prompts.append(prompt)
            if self.fail:
                raise self.fail
            newest = prompt.split("<message>")[-1].split("</message>")[0]
            return schema.model_validate(self.decisions[newest])

        return RunnableLambda(respond)


def decision(intent: str, confidence: float = 0.9, **slots: Any) -> dict[str, Any]:
    base = dict.fromkeys(("topic", "source", "output", "memory_action", "memory_content"))
    return {"intent": intent, "confidence": confidence, "slots": {**base, **slots}}


# -- the dataset -----------------------------------------------------------------------------------


def test_the_dataset_covers_every_intent_with_at_least_twenty_cases_each() -> None:
    cases = load_dataset("router.intents", RouterCase).cases
    counts = Counter(c.expected_intent for c in cases)
    assert set(counts) == {"general_chat", "manage_memory", "generate_document", "deep_research"}
    assert all(n >= 20 for n in counts.values()), counts


def test_the_smoke_subset_has_every_intent() -> None:
    smoke = load_dataset("router.intents", RouterCase, smoke_only=True).cases
    assert {c.expected_intent for c in smoke} == {
        "general_chat",
        "manage_memory",
        "generate_document",
        "deep_research",
    }


def test_the_dataset_has_the_cases_the_eval_plan_names() -> None:
    cases = {c.input: c for c in load_dataset("router.intents", RouterCase).cases}
    # casual mention vs explicit request (GAP-09), near-misses, follow-ups, compound precedence
    assert (
        cases["I mostly write Go these days, how do I read a file?"].expected_intent
        == "general_chat"
    )
    assert cases["Remember that my timezone is IST."].expected_slots_contains == {
        "memory_action": "save",
        "memory_content": "IST",
    }
    assert cases["What is a PDF, technically?"].expected_intent == "general_chat"
    assert (
        cases["Forget the Python approach, let's use Rust for this script."].expected_intent
        == "general_chat"
    )
    assert cases["What can you do?"].expected_intent == "general_chat"
    assert cases["Make that a PDF."].expected_slots_contains == {"source": "last_answer"}
    assert cases["Turn this conversation into a PDF."].expected_slots_contains == {
        "source": "thread"
    }
    assert cases[
        "Research the pros and cons of Rust and then make a PDF of it."
    ].expected_slots_contains == {"output": "pdf"}
    assert (
        cases["Do deep research on how vector databases scale."].expected_intent == "deep_research"
    )


def test_follow_up_cases_carry_the_history_they_need() -> None:
    follow_ups = [
        c
        for c in load_dataset("router.intents", RouterCase).cases
        if c.expected_slots_contains.get("source") in ("last_answer", "thread")
        and c.input.lower() not in ("pdf of our conversation pls",)
    ]
    assert follow_ups and all(c.history for c in follow_ups)


def test_no_record_contains_an_email_address_or_a_secret() -> None:
    text = (DATASETS_DIR / "router.intents.jsonl").read_text()
    assert "@" not in text and "sk-" not in text


def test_the_record_model_rejects_inconsistent_records() -> None:
    with pytest.raises(ValidationError):
        case(acceptable_intents=["general_chat"])  # the expected one is not an alternative
    with pytest.raises(ValidationError):
        case(expected_slots_contains={"nonsense": "x"})
    with pytest.raises(ValidationError):
        case(expected_slots_contains={"topic": "x"})  # general_chat has no slots
    with pytest.raises(ValidationError):
        case(history=[{"role": "user", "content": "x", "intent": "general_chat"}])


# -- helpers ---------------------------------------------------------------------------------------


def test_history_becomes_graph_messages_and_the_previous_intent_is_the_last_stated() -> None:
    c = case(
        history=[
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "intent": "manage_memory"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
    )
    messages = history_messages(c)
    assert [type(m) for m in messages] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert previous_intent(c) == "manage_memory"
    assert previous_intent(case()) is None


def test_slots_must_contain_the_expected_text_case_insensitively() -> None:
    c = case(
        expected_intent="manage_memory",
        expected_slots_contains={"memory_action": "forget", "memory_content": "acme"},
    )
    good = Route(
        "manage_memory",
        "router",
        0.9,
        {"memory_action": "forget", "memory_content": "Works at ACME"},
    )
    bad = Route("manage_memory", "router", 0.9, {"memory_action": "save", "memory_content": "Acme"})
    assert slots_match(c, good) and not slots_match(c, bad)


def test_macro_f1_over_the_intents_that_occur() -> None:
    perfect = [("general_chat", "general_chat"), ("manage_memory", "manage_memory")]
    assert macro_f1(perfect) == 1.0
    half = [("general_chat", "general_chat"), ("manage_memory", "general_chat")]
    # general_chat: tp1 fp1 -> 2/3; manage_memory: fn1 -> 0
    assert macro_f1(half) == pytest.approx(1 / 3)
    assert macro_f1([]) is None


# -- the step --------------------------------------------------------------------------------------


async def run(cases: list[RouterCase], model: Any) -> SuiteRun:
    from evals.datasets import Dataset

    suite = make_suite()
    await run_router_intents(suite, Dataset("router.intents", "v", cases), model=model)
    return suite


async def test_right_and_wrong_routes_are_scored_and_the_report_has_the_confusion_matrix() -> None:
    cases = [
        case(id="ri-901", input="hi"),
        case(
            id="ri-902",
            input="Remember that I like tea",
            expected_intent="manage_memory",
            expected_slots_contains={"memory_action": "save", "memory_content": "tea"},
        ),
        case(id="ri-903", input="make that a pdf", expected_intent="generate_document"),
    ]
    model = Scripted(
        {
            "hi": decision("general_chat", 0.95),
            "Remember that I like tea": decision(
                "manage_memory", 0.9, memory_action="save", memory_content="User likes tea."
            ),
            "make that a pdf": decision("general_chat", 0.8),  # wrong
        }
    )

    suite = await run(cases, model)

    metrics, skipped = _metrics(suite.scores, ["router.intents"])
    assert metrics["router.intent_accuracy"] == pytest.approx(2 / 3)
    assert metrics["router.slot_accuracy"] == 1.0
    assert metrics["router.fallback_rate"] == 0.0
    assert 0 < metrics["router.macro_f1"] < 1
    assert "router.latency_p95_ms" in metrics and skipped == []
    report = suite.extras["router"]
    assert report["confusion"]["generate_document"] == {"general_chat": 1}
    assert report["min_confidence"] == 0.70
    assert report["mean_confidence_right"] == pytest.approx(0.925)
    assert report["mean_confidence_wrong"] == pytest.approx(0.8)


async def test_an_acceptable_alternative_counts_as_right() -> None:
    c = case(
        input="make a pdf of my notes",
        expected_intent="generate_document",
        acceptable_intents=["general_chat"],
    )
    suite = await run([c], Scripted({"make a pdf of my notes": decision("general_chat", 0.9)}))

    assert suite.scores.mean("router.intent_accuracy") == 1.0
    # an accepted alternative is not a confusion: the pair is (general_chat, general_chat)
    assert suite.extras["router"]["confusion"]["general_chat"] == {"general_chat": 1}
    assert suite.extras["router"]["confusion"]["generate_document"] == {}


async def test_a_wrong_slot_is_a_slot_miss_only_when_the_intent_is_right() -> None:
    c = case(
        input="forget that I work at Acme",
        expected_intent="manage_memory",
        expected_slots_contains={"memory_action": "forget"},
    )
    wrong_slot = Scripted(
        {
            "forget that I work at Acme": decision(
                "manage_memory", 0.9, memory_action="save", memory_content="x"
            )
        }
    )
    wrong_intent = Scripted({"forget that I work at Acme": decision("general_chat", 0.9)})

    assert (await run([c], wrong_slot)).scores.mean("router.slot_accuracy") == 0.0
    assert (await run([c], wrong_intent)).scores.mean("router.slot_accuracy") is None


async def test_low_confidence_counts_as_a_fallback_and_a_miss() -> None:
    c = case(input="remember I like tea", expected_intent="manage_memory")
    suite = await run(
        [c],
        Scripted(
            {
                "remember I like tea": decision(
                    "manage_memory", 0.4, memory_action="save", memory_content="x"
                )
            }
        ),
    )

    assert suite.scores.mean("router.fallback_rate") == 1.0
    assert suite.scores.mean("router.intent_accuracy") == 0.0


async def test_a_failed_call_is_a_miss_and_cannot_raise_a_metric() -> None:
    suite = await run([case(input="hi")], Scripted({}, fail=RuntimeError("down")))

    assert suite.scores.mean("router.intent_accuracy") == 0.0
    assert suite.errors == 1
    assert suite.scores.mean("router.latency_p95_ms") is None  # no latency for a failed call


async def test_the_router_is_shown_user_text_and_the_previous_intent_only() -> None:
    c = case(
        input="make that a pdf",
        expected_intent="generate_document",
        history=[
            {"role": "user", "content": "explain RAG"},
            {"role": "assistant", "content": "ASSISTANT-WORDS", "intent": "general_chat"},
        ],
    )
    model = Scripted({"make that a pdf": decision("generate_document", 0.9, source="last_answer")})

    await run([c], model)

    assert "ASSISTANT-WORDS" not in model.prompts[0]
    assert "<message>explain RAG</message>" in model.prompts[0]
    assert "<previous_intent>general_chat</previous_intent>" in model.prompts[0]


def test_a_dataset_file_with_a_bad_record_is_reported(tmp_path: Any) -> None:
    (tmp_path / "router.intents.jsonl").write_text(
        json.dumps({"id": "ri-1", "input": "x", "expected_intent": "teleport"}) + "\n"
    )
    with pytest.raises(DatasetError):
        load_dataset("router.intents", RouterCase, directory=tmp_path)
