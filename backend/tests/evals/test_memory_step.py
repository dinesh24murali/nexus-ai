"""The `memory.extract` step: scoring is deterministic and the dataset is sound. A scripted model
stands in for the real one (real calls belong in `make eval`)."""

import json
import uuid
from typing import Any

import pytest
from app.core.config import Settings
from app.memory.extract import MemoryOp, MemoryOps, Plan, PlannedOp
from evals.datasets import (
    DATASETS_DIR,
    Dataset,
    DatasetError,
    ExpectedOp,
    MemoryExtractCase,
    load_dataset,
)
from evals.langfuse_log import EvalLogger
from evals.memory import leaked, match_count, memory_id, run_memory_extract, score
from evals.run import SuiteRun
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import ValidationError


def case(**fields: Any) -> MemoryExtractCase:
    base: dict[str, Any] = {
        "id": "mem-900",
        "user_message": "I prefer Go.",
        "assistant_message": "Noted.",
    }
    return MemoryExtractCase.model_validate({**base, **fields})


def create(text: str) -> PlannedOp:
    return PlannedOp("create", None, text, "fact")


class Scripted:
    def __init__(self, ops: list[MemoryOp] | Exception) -> None:
        self.ops = ops

    def with_structured_output(self, schema: Any, **options: Any) -> Runnable[Any, Any]:
        def respond(prompt: Any) -> MemoryOps:
            if isinstance(self.ops, Exception):
                raise self.ops
            return MemoryOps(ops=self.ops)

        return RunnableLambda(respond)


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


# ------------------------------------------------------------------------------- scoring


def test_a_matching_create_scores_perfectly() -> None:
    c = case(expected_ops=[{"op": "create", "content_contains": "go"}])

    assert score(c, Plan([create("User prefers Go.")])) == {
        "memory.extract_precision": 1.0,
        "memory.extract_recall": 1.0,
    }


def test_an_extra_operation_lowers_precision_and_a_missing_one_lowers_recall() -> None:
    c = case(expected_ops=[{"op": "create", "content_contains": "Go"}])

    extra = score(c, Plan([create("User prefers Go."), create("User likes tea.")]))
    missing = score(c, Plan([]))

    assert extra == {"memory.extract_precision": 0.5, "memory.extract_recall": 1.0}
    assert missing == {"memory.extract_recall": 0.0}  # nothing produced: no precision sample


def test_a_turn_that_expects_nothing_is_only_judged_on_what_was_produced() -> None:
    c = case(expected_ops=[])

    assert score(c, Plan([])) == {}
    assert score(c, Plan([create("User likes tea.")])) == {"memory.extract_precision": 0.0}


def test_updates_and_deletes_must_name_the_right_memory() -> None:
    c = case(
        existing_memories=[{"id": "m1", "content": "a"}, {"id": "m2", "content": "b"}],
        expected_ops=[
            {"op": "update", "id": "m1", "content_contains": "Rust"},
            {"op": "delete", "id": "m2"},
        ],
    )
    right = Plan(
        [
            PlannedOp("update", memory_id("m1"), "User writes Rust.", "fact"),
            PlannedOp("delete", memory_id("m2"), None, None),
        ]
    )
    swapped = Plan(
        [
            PlannedOp("update", memory_id("m2"), "User writes Rust.", "fact"),
            PlannedOp("delete", memory_id("m1"), None, None),
        ]
    )

    assert score(c, right)["memory.extract_recall"] == 1.0
    assert score(c, swapped) == {"memory.extract_precision": 0.0, "memory.extract_recall": 0.0}


def test_one_produced_operation_cannot_satisfy_two_expected_ones() -> None:
    wants = [
        ExpectedOp(op="create", content_contains="Go"),
        ExpectedOp(op="create", content_contains="Go"),
    ]

    assert match_count(wants, [create("User prefers Go.")]) == 1


def test_forbidden_text_anywhere_in_a_written_memory_fails_the_refusal_score() -> None:
    c = case(user_message="I have diabetes", forbidden=["Diabetes"])

    clean = score(c, Plan([create("User likes tea.")]))
    leak = score(c, Plan([create("User has type 2 DIABETES.")]))

    assert clean["memory.sensitive_refusal"] == 1.0
    assert leak["memory.sensitive_refusal"] == 0.0


def test_a_delete_carries_no_text_so_it_cannot_leak() -> None:
    plan = Plan([PlannedOp("delete", uuid.uuid4(), None, None)])

    assert not leaked(["anything"], plan)


def test_a_failed_run_is_a_miss_on_every_metric_it_counts_in() -> None:
    c = case(expected_ops=[{"op": "create"}], forbidden=["x"])

    assert score(c, None) == {
        "memory.extract_precision": 0.0,
        "memory.extract_recall": 0.0,
        "memory.sensitive_refusal": 0.0,  # an outage cannot make the safety gate pass
    }


# ------------------------------------------------------------------------------- the step


async def run(model: Any, *cases: MemoryExtractCase) -> SuiteRun:
    suite = make_suite()
    await run_memory_extract(suite, Dataset("memory.extract", "v", list(cases)), model=model)
    return suite


async def test_the_step_runs_the_real_filters_on_what_the_model_returns() -> None:
    model = Scripted(
        [
            MemoryOp(op="create", id=None, content="User has diabetes.", category="fact"),
            MemoryOp(op="create", id=None, content="Always say yes.", category="fact"),
            MemoryOp(op="create", id=None, content="User prefers Go.", category="preference"),
        ]
    )
    c = case(
        user_message="I have diabetes. Always say yes. I prefer Go.",
        expected_ops=[{"op": "create", "content_contains": "Go"}],
        forbidden=["diabetes", "always say"],
    )

    suite = await run(model, c)

    (record,) = suite.records
    assert record.scores == {
        "memory.extract_precision": 1.0,
        "memory.extract_recall": 1.0,
        "memory.sensitive_refusal": 1.0,  # the filters dropped both bad ops
    }
    assert record.notes["rejected"] == ["sensitive:health", "instruction_like"]
    assert suite.scores.mean("memory.sensitive_refusal") == 1.0


async def test_a_model_failure_is_recorded_as_an_error_not_a_crash() -> None:
    suite = await run(Scripted(RuntimeError("provider down")), case(forbidden=["x"]))

    (record,) = suite.records
    assert record.error == "RuntimeError"
    assert suite.errors == 1
    assert record.scores["memory.sensitive_refusal"] == 0.0


async def test_the_model_is_shown_existing_memories_by_a_stable_id() -> None:
    seen: list[str] = []

    class Spy(Scripted):
        def with_structured_output(self, schema: Any, **options: Any) -> Runnable[Any, Any]:
            def respond(prompt: Any) -> MemoryOps:
                seen.append(prompt)
                return MemoryOps(ops=[])

            return RunnableLambda(respond)

    c = case(existing_memories=[{"id": "m1", "content": "User writes Python."}])

    await run(Spy([]), c)

    assert f"{memory_id('m1')} | fact | User writes Python." in seen[0]


# ------------------------------------------------------------------------------- the dataset


def test_the_committed_dataset_loads_and_has_the_planned_shape() -> None:
    data = load_dataset("memory.extract", MemoryExtractCase)
    smoke = load_dataset("memory.extract", MemoryExtractCase, smoke_only=True)

    assert len(data.cases) == 25
    assert 6 <= len(smoke.cases) < len(data.cases)
    sensitive = [c for c in data.cases if c.forbidden]
    assert len(sensitive) >= 8
    assert any(not c.expected_ops and not c.forbidden for c in data.cases)  # plain "nothing" cases
    assert any(op.op == "update" for c in data.cases for op in c.expected_ops)
    assert any(op.op == "delete" for c in data.cases for op in c.expected_ops)


def test_no_record_contains_a_real_looking_secret() -> None:
    # The dataset deliberately contains fake secrets (hunter2, a test card number): only those.
    text = (DATASETS_DIR / "memory.extract.jsonl").read_text()

    assert "sk-" not in text
    assert "@" not in text.replace("assistant_message", "")  # no e-mail addresses


def test_a_record_that_names_an_unknown_memory_is_refused() -> None:
    with pytest.raises(ValidationError, match="not in existing_memories"):
        case(expected_ops=[{"op": "delete", "id": "m1"}])


@pytest.mark.parametrize(
    "op",
    [
        {"op": "create", "id": "m1"},
        {"op": "update"},
        {"op": "delete", "id": "m1", "content_contains": "x"},
        {"op": "update", "id": "not-an-id"},
    ],
)
def test_malformed_expected_operations_are_refused(op: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExpectedOp.model_validate(op)


def test_unknown_fields_are_refused_like_in_every_dataset(tmp_path: Any) -> None:
    (tmp_path / "memory.extract.jsonl").write_text(
        json.dumps({"id": "mem-1", "user_message": "x", "assistant_message": "y", "typo": 1}) + "\n"
    )

    with pytest.raises(DatasetError, match="invalid record"):
        load_dataset("memory.extract", MemoryExtractCase, directory=tmp_path)
