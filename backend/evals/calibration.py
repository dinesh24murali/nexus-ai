"""Judge calibration (TASK-037, doc 09 section 4): is `eval.judge.faithfulness` as reliable as a
human reader?

A person labels at least 30 RAG answers `faithful` (every claim is supported by the passages) or
`unfaithful`. The judge scores the same answers; an answer counts as faithful for the judge only
when all its claims are supported. Cohen's kappa between the two label lists says how much they
agree beyond chance. At kappa >= 0.7 on at least 30 answers `rag.faithfulness` may move from an
advisory metric to a regression gate. This module only reports that; changing the baseline is a
decision for the developer (ADR-0009).

    python -m evals.calibrate    # needs OPENAI_API_KEY and evals/datasets/judge.calibration.jsonl
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.config import get_settings
from app.llm.factory import LLMConfigError, get_model

from evals.datasets import (
    DATASETS_DIR,
    REPO_ROOT,
    CalibrationCase,
    Dataset,
    DatasetError,
    load_dataset,
)
from evals.judges import Judges

DATASET_NAME = "judge.calibration"
MIN_LABELS = 30  # doc 09 section 4
KAPPA_TARGET = 0.7
RESULTS_FILE = REPO_ROOT / "evals" / "results" / "calibration.json"

Decision = Literal["promote", "keep_advisory"]


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Agreement of two binary raters beyond chance. None when it is undefined, which happens
    when both raters used a single, identical label for every item."""
    if len(a) != len(b):
        raise ValueError("both label lists need the same length")
    if not a:
        raise ValueError("need at least one label")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    yes_a, yes_b = sum(a) / n, sum(b) / n
    expected = yes_a * yes_b + (1 - yes_a) * (1 - yes_b)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def decide(*, n: int, kappa: float | None) -> tuple[Decision, str]:
    if n < MIN_LABELS:
        return "keep_advisory", f"only {n} scored labels; at least {MIN_LABELS} are needed"
    if kappa is None:
        return "keep_advisory", "kappa is undefined: every label and verdict was the same"
    if kappa < KAPPA_TARGET:
        return "keep_advisory", f"kappa {kappa:.2f} is below {KAPPA_TARGET}"
    return "promote", f"kappa {kappa:.2f} on {n} labels meets {KAPPA_TARGET}"


@dataclass(frozen=True)
class CalibrationReport:
    n: int  # labelled answers the judge scored
    kappa: float | None
    agreement: float | None  # share of answers where judge and human agree
    disagreements: list[str]
    judge_failures: int  # answers the judge could not score (left out of n)
    decision: Decision
    reason: str
    judge_model: str = ""
    dataset_version: str = ""
    matrix: dict[str, int] = field(default_factory=dict)  # human/judge label pairs


async def run_calibration(
    cases: Sequence[CalibrationCase],
    judges: Judges | Any,
    *,
    judge_model: str = "",
    dataset_version: str = "",
) -> CalibrationReport:
    human: list[bool] = []
    judged: list[bool] = []
    disagreements: list[str] = []
    failures = 0
    for case in cases:
        score = await judges.faithfulness(case.question, case.passages, case.answer)
        if score is None:
            failures += 1
            continue
        human.append(case.label == "faithful")
        judged.append(score == 1.0)  # faithful only if every claim is supported
        if human[-1] != judged[-1]:
            disagreements.append(case.id)

    n = len(human)
    kappa = cohen_kappa(human, judged) if n else None
    decision, reason = decide(n=n, kappa=kappa)
    pairs = zip(human, judged, strict=True)
    matrix = {
        "human_faithful_judge_faithful": 0,
        "human_faithful_judge_unfaithful": 0,
        "human_unfaithful_judge_faithful": 0,
        "human_unfaithful_judge_unfaithful": 0,
    }
    for h, j in pairs:
        matrix[
            f"human_{'faithful' if h else 'unfaithful'}_judge_{'faithful' if j else 'unfaithful'}"
        ] += 1
    return CalibrationReport(
        n=n,
        kappa=kappa,
        agreement=(n - len(disagreements)) / n if n else None,
        disagreements=disagreements,
        judge_failures=failures,
        decision=decision,
        reason=reason,
        judge_model=judge_model,
        dataset_version=dataset_version,
        matrix=matrix,
    )


def load_calibration(directory: Path | None = None) -> Dataset[CalibrationCase]:
    base = directory or DATASETS_DIR
    path = base / f"{DATASET_NAME}.jsonl"
    if not path.exists():
        raise DatasetError(
            f"{path} not found: a person must hand-label {MIN_LABELS}+ RAG answers first "
            "(records: id, question, passages, answer, label faithful|unfaithful)"
        )
    return load_dataset(DATASET_NAME, CalibrationCase, directory=base)


def write_report(report: CalibrationReport, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")


def _summary(report: CalibrationReport) -> str:
    kappa = "undefined" if report.kappa is None else f"{report.kappa:.3f}"
    lines = [
        f"judge calibration: kappa {kappa} on {report.n} labelled answers "
        f"({report.judge_failures} unscored)",
        f"  decision: {report.decision} ({report.reason})",
    ]
    if report.disagreements:
        lines.append("  disagreements: " + ", ".join(report.disagreements))
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.llm_mode == "fake":
        print(
            "error: LLM_MODE=fake cannot calibrate a judge: the fake model has no judgement",
            file=sys.stderr,
        )
        return 2
    try:
        dataset = load_calibration(args.dataset_dir)
        judges = Judges(get_model("judge", settings=settings))
    except (DatasetError, LLMConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = await run_calibration(
        dataset.cases,
        judges,
        judge_model=settings.llm_judge_model,
        dataset_version=dataset.version,
    )
    out: Path = args.out or RESULTS_FILE
    write_report(report, out)
    print(_summary(report))
    print(f"wrote {out}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate eval.judge.faithfulness (Cohen's kappa)"
    )
    parser.add_argument("--dataset-dir", type=Path, help="folder holding judge.calibration.jsonl")
    parser.add_argument(
        "--out", type=Path, help="results file (default: evals/results/calibration.json)"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
