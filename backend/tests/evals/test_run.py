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

from evals import run as run_module
from tests.evals.test_judges import judge_returning

SYSTEM_PROMPT = load_prompt("nexus.system").body


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(_env_file=None, app_env="test", llm_mode="fake")


def graph(model: Any = None) -> Any:
    return build_graph(
        checkpointer=None, chat_model=model or FakeChatModel(), summarize_model=FakeChatModel()
    )


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
    ]
    assert report.metrics["safety.system_prompt_leaks"] == 0


async def test_with_a_judge_every_metric_is_measured(fake_settings: Settings) -> None:
    judges = judge_all()
    report = await suite(fake_settings, judges=judges)
    assert report.run["skipped_metrics"] == []
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
    with pytest.raises(SystemExit):
        parse_args(["--suite", "full", "--runs", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--suite", "weekly"])


def test_main_in_fake_mode_writes_a_fake_results_file(
    fake_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(run_module, "get_settings", lambda: fake_settings)
    out = tmp_path / "r.json"
    assert run_module.main(["--suite", "smoke", "--out", str(out)]) == 0
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
