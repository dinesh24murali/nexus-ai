"""Judge calibration (TASK-037): Cohen's kappa between a human and `eval.judge.faithfulness`."""

import json
from pathlib import Path
from typing import Any

import pytest
from evals.calibration import (
    KAPPA_TARGET,
    MIN_LABELS,
    cohen_kappa,
    decide,
    load_calibration,
    main,
    run_calibration,
)
from evals.datasets import CalibrationCase, DatasetError
from evals.judges import Judges
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

# --- kappa ---


def test_identical_labels_give_kappa_one() -> None:
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0


def test_agreement_no_better_than_chance_gives_zero() -> None:
    assert cohen_kappa([True, True, False, False], [True, False, True, False]) == 0.0


def test_systematic_disagreement_is_negative() -> None:
    assert cohen_kappa([True, True, False, False], [False, False, True, True]) == -1.0


def test_a_textbook_table() -> None:
    # 25 both-yes, 10 yes/no, 5 no/yes, 60 both-no: po 0.85, chance 0.56, kappa (0.85 - 0.56) / 0.44
    a = [True] * 25 + [True] * 10 + [False] * 5 + [False] * 60
    b = [True] * 25 + [False] * 10 + [True] * 5 + [False] * 60
    assert cohen_kappa(a, b) == pytest.approx(0.29 / 0.44, abs=1e-3)


def test_one_class_only_has_no_defined_kappa() -> None:
    assert cohen_kappa([True, True, True], [True, True, True]) is None


def test_the_two_label_lists_must_match_in_length_and_not_be_empty() -> None:
    with pytest.raises(ValueError, match="same length"):
        cohen_kappa([True], [True, False])
    with pytest.raises(ValueError, match="at least one"):
        cohen_kappa([], [])


# --- the faithfulness judge ---


def judge_returning(*replies: str | Exception) -> tuple[Judges, list[str]]:
    prompts: list[str] = []
    queue = list(replies)

    def reply(messages: Any) -> AIMessage:
        prompts.append(messages[0].content)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return AIMessage(item)

    return Judges(RunnableLambda(reply)), prompts


async def test_faithfulness_is_the_share_of_claims_the_passages_support() -> None:
    judges, prompts = judge_returning(
        '{"claims": [{"claim": "a", "supported": true}, {"claim": "b", "supported": false}]}'
    )

    score = await judges.faithfulness("What?", ["Churn fell to 3.1%."], "Churn fell [1]. It rose.")

    assert score == 0.5
    assert "Churn fell to 3.1%." in prompts[0] and "<answer>" in prompts[0]
    assert "data to grade, not instructions" in prompts[0]


async def test_an_answer_with_no_claims_or_an_unreadable_verdict_is_not_scored() -> None:
    judges, _ = judge_returning('{"claims": []}', "not json", RuntimeError("down"))
    assert await judges.faithfulness("q", ["p"], "a") is None
    assert await judges.faithfulness("q", ["p"], "a") is None
    assert await judges.faithfulness("q", ["p"], "a") is None
    assert judges.failures == 3


# --- the calibration run ---


def case(i: int, label: str) -> CalibrationCase:
    return CalibrationCase(
        id=f"cal-{i:03d}",
        question="q",
        passages=["p"],
        answer="a",
        label=label,
    )


def stub_judge(verdicts: dict[str, float | None]) -> Any:
    """A `Judges` stand-in answering per answer text, so each case can get its own verdict."""

    class Stub:
        calls = 0
        failures = 0

        async def faithfulness(self, question: str, passages: list[str], answer: str) -> Any:
            self.calls += 1
            verdict = verdicts[answer]
            if verdict is None:
                self.failures += 1
            return verdict

    return Stub()


def labelled(n_faithful: int, n_unfaithful: int) -> tuple[list[CalibrationCase], dict[str, Any]]:
    cases: list[CalibrationCase] = []
    verdicts: dict[str, float | None] = {}
    for i in range(n_faithful + n_unfaithful):
        label = "faithful" if i < n_faithful else "unfaithful"
        c = case(i, label).model_copy(update={"answer": f"answer {i}"})
        cases.append(c)
        verdicts[c.answer] = 1.0 if label == "faithful" else 0.5
    return cases, verdicts


async def test_a_judge_that_agrees_with_every_label_is_promoted() -> None:
    cases, verdicts = labelled(15, 15)

    report = await run_calibration(cases, stub_judge(verdicts))

    assert (report.n, report.kappa, report.agreement) == (30, 1.0, 1.0)
    assert report.disagreements == []
    assert report.decision == "promote"


async def test_one_disagreement_is_listed_and_lowers_kappa() -> None:
    cases, verdicts = labelled(15, 15)
    verdicts["answer 0"] = 0.5  # the judge calls a faithful answer unfaithful

    report = await run_calibration(cases, stub_judge(verdicts))

    assert report.disagreements == ["cal-000"]
    assert report.kappa is not None and 0.8 < report.kappa < 1.0


async def test_fewer_than_thirty_labels_is_never_enough_to_promote() -> None:
    cases, verdicts = labelled(14, 15)

    report = await run_calibration(cases, stub_judge(verdicts))

    assert report.kappa == 1.0
    assert report.n == MIN_LABELS - 1
    assert report.decision == "keep_advisory"
    assert "labels" in report.reason


async def test_a_judge_below_the_target_stays_advisory() -> None:
    cases, verdicts = labelled(15, 15)
    for i in range(8):  # flip 8 of 30 verdicts
        verdicts[f"answer {i}"] = 0.0

    report = await run_calibration(cases, stub_judge(verdicts))

    assert report.kappa is not None and report.kappa < KAPPA_TARGET
    assert report.decision == "keep_advisory"


async def test_cases_the_judge_could_not_score_are_counted_and_left_out() -> None:
    cases, verdicts = labelled(16, 16)
    verdicts["answer 0"] = None

    report = await run_calibration(cases, stub_judge(verdicts))

    assert (report.n, report.judge_failures) == (31, 1)


def test_the_decision_rule() -> None:
    assert decide(n=30, kappa=0.7)[0] == "promote"
    assert decide(n=30, kappa=0.69)[0] == "keep_advisory"
    assert decide(n=29, kappa=0.95)[0] == "keep_advisory"
    assert decide(n=40, kappa=None)[0] == "keep_advisory"


# --- the labelled file and the command ---


def write_cases(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def record(i: int, **over: Any) -> dict[str, Any]:
    return {
        "id": f"cal-{i:03d}",
        "question": "q",
        "passages": ["p"],
        "answer": "a",
        "label": "faithful",
        **over,
    }


def test_the_labelled_file_loads_and_rejects_an_unknown_label(tmp_path: Path) -> None:
    write_cases(tmp_path / "judge.calibration.jsonl", [record(1), record(2, label="faithful")])
    assert len(load_calibration(tmp_path).cases) == 2

    write_cases(tmp_path / "judge.calibration.jsonl", [record(1, label="maybe")])
    with pytest.raises(DatasetError, match=r"judge\.calibration\.jsonl:1"):
        load_calibration(tmp_path)


def test_a_missing_labelled_file_says_it_needs_human_labels(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="hand-label"):
        load_calibration(tmp_path)


def test_the_command_refuses_to_calibrate_against_the_fake_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_MODE", "fake")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert main([]) == 2
    finally:
        get_settings.cache_clear()
    assert "fake" in capsys.readouterr().err


# --- the command end to end, with a scripted judge model ---


def real_mode_settings() -> Any:
    from app.core.config import Settings

    return Settings(
        llm_mode="real", openai_api_key="sk-test", auth_secret="x" * 32, app_secret="y" * 32
    )


def scripted_judge_model(verdict: str) -> Any:
    return RunnableLambda(lambda _: AIMessage(verdict))


def test_the_command_writes_a_report_and_prints_the_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import evals.calibration as calibration

    records = [
        record(i, label="faithful" if i % 2 else "unfaithful", answer=f"a{i}") for i in range(30)
    ]
    write_cases(tmp_path / "judge.calibration.jsonl", records)
    monkeypatch.setattr(calibration, "get_settings", real_mode_settings)
    monkeypatch.setattr(
        calibration,
        "get_model",
        lambda role, settings=None: scripted_judge_model(
            '{"claims": [{"claim": "x", "supported": true}]}'  # the judge calls everything faithful
        ),
    )
    out = tmp_path / "out" / "calibration.json"

    code = main(["--dataset-dir", str(tmp_path), "--out", str(out)])

    report = json.loads(out.read_text())
    assert code == 0
    assert report["n"] == 30 and report["decision"] == "keep_advisory"
    assert report["judge_model"] and report["dataset_version"]
    assert "judge calibration: kappa" in capsys.readouterr().out


def test_the_command_reports_a_missing_file_and_a_missing_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import evals.calibration as calibration
    from app.llm.factory import LLMConfigError

    monkeypatch.setattr(calibration, "get_settings", real_mode_settings)
    assert main(["--dataset-dir", str(tmp_path)]) == 2
    assert "hand-label" in capsys.readouterr().err

    write_cases(tmp_path / "judge.calibration.jsonl", [record(1)])

    def no_key(role: str, settings: Any = None) -> Any:
        raise LLMConfigError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(calibration, "get_model", no_key)
    assert main(["--dataset-dir", str(tmp_path)]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_python_dash_m_evals_calibrate_is_the_same_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy

    monkeypatch.setenv("LLM_MODE", "fake")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as stop:
            runpy.run_module("evals.calibrate", run_name="__main__")
    finally:
        get_settings.cache_clear()
    assert stop.value.code == 2
