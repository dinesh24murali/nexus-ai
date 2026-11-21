"""The `tools.routing` step: scripted model, stub retriever and searcher. No network."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.graph.builder import build_graph
from app.graph.nodes.load_context import RecentFiles
from app.graph.tools.web_search import WebSearcher
from app.llm.fake import FakeChatModel
from app.rag.retriever import Searcher
from evals.datasets import DATASETS_DIR, Dataset, DatasetError, ToolsCase, load_dataset
from evals.langfuse_log import EvalLogger
from evals.run import SuiteRun
from evals.tools import approval_probes, run_tools_routing
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

NO_TOOL = "no tool"


def scripted(retriever: Searcher, web: WebSearcher, recent: RecentFiles) -> Any:
    return build_graph(
        checkpointer=InMemorySaver(),
        chat_model=FakeChatModel(),
        summarize_model=FakeChatModel(role="summarize"),
        retriever=retriever,
        web_searcher=web,
        calculator=True,
        recent_files=recent,
    )


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


def case(id_: str, text: str, tool: str | None, **fields: Any) -> ToolsCase:
    interrupt = "web_search" in (fields.get("expected_sequence") or [tool])
    return ToolsCase(
        id=id_,
        input=text,
        expected_tool=tool,
        expect_interrupt=fields.pop("expect_interrupt", interrupt),
        **fields,
    )


async def run(cases: list[ToolsCase], factory: Callable[..., Any] = scripted) -> SuiteRun:
    suite = make_suite()
    await run_tools_routing(suite, Dataset("tools.routing", "v", cases), graph_factory=factory)
    return suite


def score(suite: SuiteRun, key: str) -> float | None:
    return suite.scores.mean(key)


# -- the dataset ----------------------------------------------------------------------------------


def test_the_committed_dataset_loads_and_has_the_planned_shape() -> None:
    data = load_dataset("tools.routing", ToolsCase)

    assert len(data.cases) == 30
    assert sum(c.smoke for c in data.cases) == 10
    assert sum(c.expected_tool is None for c in data.cases) >= 8  # doc 09 section 9, GAP-06
    web = [c for c in data.cases if c.expected_tool == "web_search"]
    assert len(web) >= 2 and all(c.expect_interrupt for c in web)
    sequences = [c for c in data.cases if c.expected_sequence]
    assert len(sequences) >= 3 and all(len(c.expected_sequence or []) >= 2 for c in sequences)
    assert {c.expected_tool for c in data.cases} == {
        None,
        "search_my_files",
        "web_search",
        "calculator",
    }


def test_the_smoke_subset_covers_every_kind_of_case() -> None:
    data = load_dataset("tools.routing", ToolsCase, smoke_only=True)

    assert {c.expected_tool for c in data.cases} == {
        None,
        "search_my_files",
        "web_search",
        "calculator",
    }
    assert any(c.expected_sequence for c in data.cases)


def test_a_case_that_needs_files_carries_them() -> None:
    data = load_dataset("tools.routing", ToolsCase)

    for c in data.cases:
        if c.expected_tool == "search_my_files" or "search_my_files" in (c.expected_sequence or []):
            assert c.setup.docs, c.id


def test_the_dataset_file_has_no_stray_fields() -> None:
    path = DATASETS_DIR / "tools.routing.jsonl"
    for line in path.read_text().splitlines():
        ToolsCase.model_validate(json.loads(line))  # extra="forbid" raises on a typo


def test_an_interrupt_is_expected_exactly_when_a_web_search_is() -> None:
    with pytest.raises(ValidationError):
        ToolsCase(id="t-1", input="x", expected_tool="web_search", expect_interrupt=False)
    with pytest.raises(ValidationError):
        ToolsCase(id="t-1", input="x", expected_tool="calculator", expect_interrupt=True)
    with pytest.raises(ValidationError):
        ToolsCase(
            id="t-1",
            input="x",
            expected_tool="search_my_files",
            expected_sequence=["search_my_files", "web_search"],
            expect_interrupt=False,
        )


def test_expected_arguments_need_an_expected_tool() -> None:
    with pytest.raises(ValidationError):
        ToolsCase(
            id="t-1",
            input="x",
            expected_tool=None,
            expected_args_contains={"query": ["a"]},
            expect_interrupt=False,
        )


def test_the_first_expected_tool_must_start_the_sequence() -> None:
    with pytest.raises(ValidationError):
        ToolsCase(
            id="t-1",
            input="x",
            expected_tool="calculator",
            expected_sequence=["web_search", "calculator"],
            expect_interrupt=True,
        )


def test_a_missing_dataset_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DatasetError):
        load_dataset("tools.routing", ToolsCase, directory=tmp_path)


# -- scoring --------------------------------------------------------------------------------------


async def test_the_right_tool_with_the_right_arguments_scores_on_every_metric() -> None:
    suite = await run(
        [
            case(
                "t-1",
                "[[fixture:web_search]] news",
                "web_search",
                expected_args_contains={"query": ["release"]},
            )
        ]
    )

    assert score(suite, "tools.selection_accuracy") == 1.0
    assert score(suite, "tools.arg_validity") == 1.0
    assert score(suite, "tools.approval_enforced") == 1.0
    assert score(suite, "tools.unnecessary_call_rate") is None  # no expected-none case ran
    assert score(suite, "tools.sequence_accuracy") is None


async def test_a_wrong_tool_misses_selection_and_argument_validity() -> None:
    suite = await run([case("t-1", "[[fixture:calc]] add", "web_search")])

    assert score(suite, "tools.selection_accuracy") == 0.0
    assert score(suite, "tools.arg_validity") == 0.0  # the expected tool was never called


async def test_a_missing_expected_text_in_the_arguments_is_invalid() -> None:
    suite = await run(
        [
            case(
                "t-1",
                "[[fixture:calc]] x",
                "calculator",
                expected_args_contains={"expression": ["999999"]},
            )
        ]
    )

    assert score(suite, "tools.selection_accuracy") == 1.0
    assert score(suite, "tools.arg_validity") == 0.0


async def test_expected_text_is_matched_without_regard_to_case() -> None:
    suite = await run(
        [
            case(
                "t-1",
                "[[fixture:web_search]] x",
                "web_search",
                expected_args_contains={"query": ["GO RELEASE"]},
            )
        ]
    )

    assert score(suite, "tools.arg_validity") == 1.0


async def test_no_tool_when_none_is_expected_is_right_and_a_call_is_unnecessary() -> None:
    suite = await run(
        [
            case("t-1", "hello there", None),
            case("t-2", "[[fixture:rag]] churn?", None),
        ]
    )

    assert score(suite, "tools.selection_accuracy") == 0.5
    assert score(suite, "tools.unnecessary_call_rate") == 0.5
    assert score(suite, "tools.arg_validity") is None  # nothing was expected to be called


async def test_a_missing_call_when_one_is_expected_is_a_miss() -> None:
    suite = await run([case("t-1", "hello there", "calculator")])

    assert score(suite, "tools.selection_accuracy") == 0.0
    assert score(suite, "tools.arg_validity") == 0.0


async def test_a_sequence_is_checked_after_approving_every_interrupt() -> None:
    suite = await run(
        [
            case(
                "t-1",
                "[[fixture:file_then_web]] go",
                "search_my_files",
                expected_sequence=["search_my_files", "web_search"],
            ),
            case(
                "t-2",
                "[[fixture:file_then_web]] go",
                "search_my_files",
                expected_sequence=["search_my_files", "calculator"],
            ),
        ]
    )

    assert score(suite, "tools.sequence_accuracy") == 0.5
    assert score(suite, "tools.selection_accuracy") == 1.0


async def test_two_searches_in_one_message_are_both_approved_and_both_counted() -> None:
    suite = await run(
        [
            case(
                "t-1",
                "[[fixture:two_searches]] go",
                "web_search",
                expected_sequence=["web_search", "web_search"],
            )
        ]
    )

    assert score(suite, "tools.sequence_accuracy") == 1.0
    assert score(suite, "tools.approval_enforced") == 1.0


async def test_the_case_files_are_what_the_search_finds() -> None:
    from evals.datasets import SetupDoc

    suite = await run(
        [
            case(
                "t-1",
                "[[fixture:rag]] churn?",
                "search_my_files",
                setup={"docs": [SetupDoc(name="q3-report.pdf", text="Churn fell to 3.1%.")]},
            )
        ]
    )

    record = suite.records[0]
    assert record.notes["calls"] == ["search_my_files"]
    assert "Churn fell to 3.1%." in record.notes["results"][0]  # what the model was shown
    assert "q3-report.pdf" in record.notes["results"][0]
    assert record.error is None


async def test_the_record_lists_what_happened_for_the_reviewer() -> None:
    suite = await run([case("t-1", "[[fixture:web_search]] x", "web_search")])

    record = suite.records[0]
    assert record.dataset == "tools.routing" and record.case_id == "t-1"
    assert record.notes["calls"] == ["web_search"]
    assert record.notes["interrupts"] == 1
    assert record.notes["first_args"] == {"query": "Go release notes"}


async def test_a_run_that_fails_is_a_miss_and_is_listed() -> None:
    def broken(retriever: Searcher, web: WebSearcher, recent: RecentFiles) -> Any:
        raise RuntimeError("model down")

    suite = await run([case("t-1", "x", "calculator")], factory=broken)

    assert suite.errors == 1
    assert score(suite, "tools.selection_accuracy") == 0.0  # an outage cannot raise a metric
    assert score(suite, "tools.arg_validity") == 0.0


# -- approval enforcement -------------------------------------------------------------------------


async def test_the_probes_need_no_model_and_every_search_was_asked_about() -> None:
    suite = make_suite()

    await approval_probes(suite)

    samples = suite.scores.samples("tools.approval_enforced")
    assert len(samples) >= 3 and set(samples) == {1.0}


async def test_a_step_always_measures_approval_even_when_no_case_searches() -> None:
    suite = await run([case("t-1", "hello", None)])

    assert score(suite, "tools.approval_enforced") == 1.0  # from the probes


async def test_a_graph_that_runs_searches_without_asking_scores_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The metric can fail: with both locks removed the search runs with no interrupt."""
    import app.graph.builder as builder
    import app.graph.nodes.tools as tools_node

    monkeypatch.setattr(builder, "calls_needing_approval", lambda *a, **k: [])
    monkeypatch.setattr(tools_node, "requires_decision", lambda call: False)

    suite = await run([case("t-1", "[[fixture:web_search]] news", "web_search")])

    assert score(suite, "tools.approval_enforced") == 0.0
    assert min(suite.scores.samples("tools.approval_enforced")) == 0.0
