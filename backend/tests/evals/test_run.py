import json
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from app.core.config import Settings
from app.graph.builder import build_graph
from app.llm.fake import FakeChatModel
from app.llm.prompts import load_prompt
from evals.judges import Judges
from evals.langfuse_log import EvalLogger
from evals.run import RUNS_PER_CASE, default_output, parse_args, run_suite, write_report
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from evals import run as run_module
from tests.evals.test_judges import judge_returning

SYSTEM_PROMPT = load_prompt("nexus.system").body


class _AnyStr:
    def __eq__(self, other: object) -> bool:
        return isinstance(other, str)


ANY_STR = _AnyStr()


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(_env_file=None, app_env="test", llm_mode="fake")


def graph(model: Any = None) -> Any:
    return build_graph(
        checkpointer=None, chat_model=model or FakeChatModel(), summarize_model=FakeChatModel()
    )


SAFETY_AND_CHAT = ["chat.golden", "summary.golden", "perf.latency", "safety.redteam"]


def judge_all() -> Judges:
    """Answers every question the suite can ask a judge, by looking at which prompt it got."""

    def reply(messages: Any) -> AIMessage:
        prompt = messages[0].content
        if "<facts>" in prompt:
            facts = [line for line in prompt.split("<facts>")[1].split("</facts>")[0].splitlines()]
            verdicts = [{"fact": f, "present": i == 0} for i, f in enumerate(f for f in facts if f)]
            return AIMessage(json.dumps({"facts": verdicts}))
        if "<request>" in prompt:
            refused = any(w in prompt for w in ("keylogger", "nerve agent", "phishing", "users"))
            return AIMessage(json.dumps({"refused": refused}))
        return AIMessage('{"score": 4}')

    return Judges(RunnableLambda(reply))


async def suite(settings: Settings, **overrides: Any) -> Any:
    args: dict[str, Any] = {
        "settings": settings,
        "graph": graph(),
        "summarizer": FakeChatModel(role="summarize"),
        "judges": None,
        "logger": EvalLogger(None, run_name="t", environment="test"),
    }
    return await run_suite(overrides.pop("suite", "smoke"), **(args | overrides))


async def test_a_smoke_run_measures_what_needs_no_judge(fake_settings: Settings) -> None:
    report = await suite(fake_settings)
    assert report.run["suite"] == "smoke" and report.run["mode"] == "fake"
    assert report.run["runs_per_case"] == 1
    assert report.run["datasets"]["chat.golden"]["cases"] == 8
    assert report.run["errors"] == 0
    assert set(report.metrics) == {
        "chat.format_compliance",
        "safety.system_prompt_leaks",
        "safety.canary_pass_rate",
        "ops.ttft_p95_ms",
        "ops.cost_per_turn_usd",
        "ops.cached_input_ratio",
    }
    # Without a judge these cannot be measured: they are absent, never a made-up number.
    assert report.run["skipped_metrics"] == [
        "chat.refusal_accuracy",
        "chat.answer_relevance",
        "summary.fact_retention",
        # The attacks that need a world did not run (no tool graph was given), so the two gated
        # safety metrics are not measured either: a gate over part of the attacks would pass for
        # the wrong reason (TASK-029).
        "safety.injection_resistance",
        "safety.cross_tenant_leaks",
    ]
    assert report.run["redteam_tool_cases_skipped"] == 8
    assert report.metrics["safety.system_prompt_leaks"] == 0


async def test_with_a_judge_every_metric_is_measured(fake_settings: Settings) -> None:
    judges = judge_all()
    # With a tool graph the attacks that need a world run too, so the gated safety metrics exist.
    report = await suite(
        fake_settings, judges=judges, tools_graph=tools_graph, only=SAFETY_AND_CHAT
    )
    assert report.run["skipped_metrics"] == []
    assert {"safety.injection_resistance", "safety.cross_tenant_leaks"} <= set(report.metrics)
    assert report.metrics["chat.answer_relevance"] == pytest.approx(0.8)
    # This judge recognises every refusal case in the smoke subset, and none of the others.
    assert report.metrics["chat.refusal_accuracy"] == 1.0
    assert report.metrics["summary.fact_retention"] == pytest.approx(1 / 5, abs=0.2)
    assert report.run["judge_failures"] == 0 and report.run["judge_calls"] > 0


async def test_a_judge_that_always_fails_leaves_its_metrics_out(fake_settings: Settings) -> None:
    broken = Judges(RunnableLambda(lambda _: AIMessage("sorry, no JSON")))
    report = await suite(fake_settings, judges=broken)
    assert "chat.answer_relevance" not in report.metrics
    assert report.run["judge_failures"] == report.run["judge_calls"] > 0


async def test_a_model_that_reveals_its_prompt_is_counted_as_a_leak(
    fake_settings: Settings,
) -> None:
    leaky: Any = RunnableLambda(lambda _: AIMessage("Sure, here it is:\n" + SYSTEM_PROMPT))
    report = await suite(fake_settings, graph=graph(leaky))
    # 2 smoke records carry the leak canary (rt-06a, rt-07a): the metric is a count, not a rate.
    assert report.metrics["safety.system_prompt_leaks"] == 2
    assert report.metrics["safety.canary_pass_rate"] < 1


async def test_failed_cases_count_against_the_format_gate_and_are_listed(
    fake_settings: Settings,
) -> None:
    def boom(_: Any) -> AIMessage:
        raise RuntimeError("down")

    report = await suite(fake_settings, graph=graph(RunnableLambda(boom)))
    assert report.metrics["chat.format_compliance"] == 0.0  # an outage cannot raise the metric
    # Every chat-graph case failed (8 + 5 + 4); the 3 summary cases use another model and did not.
    assert (report.run["errors"], report.run["case_runs"]) == (17, 20)
    assert "ops.ttft_p95_ms" in report.run["skipped_metrics"]
    assert "safety.system_prompt_leaks" in report.run["skipped_metrics"]


async def test_full_runs_each_case_n_times_and_smoke_once(fake_settings: Settings) -> None:
    assert RUNS_PER_CASE == {"smoke": 1, "full": 3}
    once = await suite(fake_settings, only=["safety.redteam"])
    thrice = await suite(fake_settings, suite="full", only=["safety.redteam"])
    assert once.run["datasets"] == {"safety.redteam": once.run["datasets"]["safety.redteam"]}
    assert once.run["case_runs"] == 4  # 4 smoke records
    assert thrice.run["runs_per_case"] == 3
    assert thrice.run["case_runs"] == 10 * 3  # every record, three times


async def test_runs_can_be_overridden(fake_settings: Settings) -> None:
    report = await suite(fake_settings, only=["safety.redteam"], runs=2)
    assert report.run["case_runs"] == 8


async def test_the_report_names_the_prompts_and_datasets_it_used(fake_settings: Settings) -> None:
    report = await suite(fake_settings, only=["summary.golden"])
    assert report.run["prompt_versions"]["nexus.system"] >= 1
    assert report.run["prompt_versions"]["eval.judge.relevance"] == 1
    assert len(report.run["datasets"]["summary.golden"]["version"]) == 12


async def test_the_summary_dataset_runs_through_the_summariser(fake_settings: Settings) -> None:
    judges, prompts = judge_returning(
        *[json.dumps({"facts": [{"fact": "x", "present": True}] * n}) for n in (5, 6, 5)]
    )
    report = await suite(fake_settings, judges=judges, only=["summary.golden"])
    assert report.metrics == {"summary.fact_retention": 1.0}
    assert all("FAKE-SUMMARY" in p for p in prompts)


async def test_results_files_have_the_shape_check_baseline_reads(
    fake_settings: Settings, tmp_path: Path
) -> None:
    report = await suite(fake_settings, only=["safety.redteam"])
    cases = write_report(report, tmp_path / "out" / "latest.json")
    data = json.loads((tmp_path / "out" / "latest.json").read_text())
    assert set(data) == {"metrics", "run"} and "safety.system_prompt_leaks" in data["metrics"]
    rows = json.loads(cases.read_text())
    assert len(rows) == 4 and {"dataset", "case", "scores", "output"} <= set(rows[0])


def test_a_fake_run_never_writes_the_file_the_gate_reads(fake_settings: Settings) -> None:
    assert default_output(fake_settings).name == "latest.fake.json"
    real = Settings(_env_file=None, app_env="test", llm_mode="real")
    assert default_output(real).name == "latest.json"


def test_argument_parsing() -> None:
    args = parse_args(["--suite", "full", "--runs", "2", "--dataset", "chat.golden"])
    assert (args.suite, args.runs, args.dataset) == ("full", 2, ["chat.golden"])
    assert args.no_rag is False
    assert parse_args(["--suite", "smoke", "--no-rag"]).no_rag is True
    assert parse_args(["--suite", "smoke", "--dataset", "rag.golden"]).dataset == ["rag.golden"]
    with pytest.raises(SystemExit):
        parse_args(["--suite", "full", "--runs", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--suite", "weekly"])


def test_main_in_fake_mode_writes_a_fake_results_file(
    fake_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(run_module, "get_settings", lambda: fake_settings)
    out = tmp_path / "r.json"
    assert run_module.main(["--suite", "smoke", "--no-rag", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["run"]["mode"] == "fake"
    assert "not measured: chat.refusal_accuracy" in capsys.readouterr().out


def test_main_without_a_key_fails_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    real = Settings(_env_file=None, app_env="test", llm_mode="real")
    monkeypatch.setattr(run_module, "get_settings", lambda: real)
    assert run_module.main(["--suite", "smoke", "--out", str(tmp_path / "r.json")]) == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()


def provider_block() -> openai.BadRequestError:
    request = httpx2.Request("POST", "https://x")
    return openai.BadRequestError(
        "400 - This content was flagged for possible cybersecurity risk.",
        response=httpx2.Response(400, request=request),
        body=None,
    )


async def test_a_provider_block_on_a_refusal_case_counts_as_a_refusal(
    fake_settings: Settings,
) -> None:
    def blocked(_: Any) -> AIMessage:
        raise provider_block()

    report = await suite(
        fake_settings, graph=graph(RunnableLambda(blocked)), only=["safety.redteam"]
    )
    # rt-06a is not a refusal case: the block there is an error. The 3 refusal cases are not.
    assert report.run["provider_blocked"] == 4
    assert report.run["errors"] == 1
    assert report.metrics["chat.refusal_accuracy"] == 1.0
    assert "safety.system_prompt_leaks" not in report.metrics  # nothing was measured, not "0"


# --- rag.golden ---


def rag_target() -> Any:
    import uuid

    from evals.runner import RagTarget

    from tests.graph.retrieval_support import StubRetriever, passage

    rag_graph = build_graph(
        checkpointer=None,
        chat_model=FakeChatModel(),
        summarize_model=FakeChatModel(role="summarize"),
        retriever=StubRetriever([passage(filename="q3-business-review.pdf", page=2)]),
    )
    return RagTarget(graph=rag_graph, user_id=uuid.uuid4())


async def test_the_rag_dataset_runs_only_when_a_rag_target_is_given(
    fake_settings: Settings,
) -> None:
    without = await suite(fake_settings)
    assert "rag.golden" not in without.run["datasets"]
    assert not any(k.startswith("rag.") for k in without.run["skipped_metrics"])

    report = await suite(fake_settings, rag=rag_target(), only=["rag.golden"])
    assert report.run["datasets"]["rag.golden"]["cases"] == 10  # the smoke subset
    assert report.run["case_runs"] == 10
    # No judge: the deterministic metrics are measured, the judged ones are listed as skipped.
    assert {"rag.context_recall", "rag.search_rate", "rag.citation_validity"} <= set(report.metrics)
    assert {"rag.faithfulness", "rag.answer_correctness", "rag.unanswerable_accuracy"} <= set(
        report.run["skipped_metrics"]
    )


async def test_the_rag_metrics_are_all_listed_when_the_suite_runs_with_a_judge(
    fake_settings: Settings,
) -> None:
    from tests.evals.test_rag_step import judges

    report = await suite(fake_settings, rag=rag_target(), judges=judges(), only=["rag.golden"])
    # The fake model never searches, so nothing was returned for faithfulness to be judged against.
    assert report.run["skipped_metrics"] == ["rag.faithfulness"]
    assert "rag.answer_correctness.whole_doc" in report.metrics


# -- tools.routing --------------------------------------------------------------------------------


def tools_graph(retriever: Any, web: Any, recent: Any, memories: Any = None) -> Any:
    return build_graph(
        checkpointer=InMemorySaver(),
        chat_model=FakeChatModel(),
        summarize_model=FakeChatModel(role="summarize"),
        retriever=retriever,
        web_searcher=web,
        calculator=True,
        recent_files=recent,
        memories=memories,
    )


async def test_the_tools_step_runs_when_it_is_given_a_graph_and_measures_its_metrics(
    fake_settings: Settings,
) -> None:
    report = await suite(fake_settings, tools_graph=tools_graph, only=["tools.routing"])

    assert report.run["datasets"] == {"tools.routing": {"version": ANY_STR, "cases": 10}}
    assert report.metrics["tools.approval_enforced"] == 1.0  # the scripted probes need no model
    assert {
        "tools.selection_accuracy",
        "tools.arg_validity",
        "tools.unnecessary_call_rate",
        "tools.sequence_accuracy",
    } <= set(report.metrics)
    assert report.run["skipped_metrics"] == []


async def test_without_a_tools_graph_the_tools_step_is_not_part_of_the_run(
    fake_settings: Settings,
) -> None:
    report = await suite(fake_settings)

    assert "tools.routing" not in report.run["datasets"]
    assert not any(key.startswith("tools.") for key in report.metrics)


async def test_the_full_suite_runs_every_tools_case_once_per_run(fake_settings: Settings) -> None:
    report = await suite(
        fake_settings, suite="full", runs=1, tools_graph=tools_graph, only=["tools.routing"]
    )

    assert report.run["datasets"]["tools.routing"]["cases"] == 30
    assert report.run["case_runs"] == 30


def test_the_tools_dataset_can_be_selected_on_the_command_line() -> None:
    assert parse_args(["--suite", "smoke", "--dataset", "tools.routing"]).dataset == [
        "tools.routing"
    ]
