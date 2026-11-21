"""Eval runner: `uv run python -m evals.run --suite smoke|full` (from `backend/`).

Runs the datasets that have a suite today (chat, summary, latency/cost, chat-only red team) through
the real graph, scores them and writes `evals/results/latest.json`:

    {"metrics": {"chat.format_compliance": 0.93, ...}, "run": {...}}

`evals/check_baseline.py` then gates on `evals/eval-baseline.json` (`make eval`, `make eval-smoke`).
A metric with no samples is left out of `metrics` and listed under `run.skipped_metrics`, so a
gated metric that could not be measured fails the gate ("missing from results") instead of passing.
`--suite smoke` runs the records tagged smoke once; `full` runs everything three times and uses
the mean (doc 09 section 1).

`LLM_MODE=fake` exercises the pipeline without a model: it writes `latest.fake.json` so a fake run
can never be gated against baselines measured on a real model, and judged metrics are skipped.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.core.config import Settings, get_settings
from app.graph.builder import build_graph
from app.llm.factory import LLMConfigError, get_model
from app.llm.prompts import all_prompts, load_prompt
from app.llm.tracing import Tracer
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from evals.canaries import check_canaries
from evals.corpus import RagEnv, RagSetupError, ingest_corpus
from evals.datasets import (
    REPO_ROOT,
    ChatCase,
    Dataset,
    DatasetError,
    MemoryExtractCase,
    PerfCase,
    RagCase,
    RedTeamCase,
    SummaryCase,
    ToolsCase,
    load_dataset,
)
from evals.judges import Judges
from evals.langfuse_log import EvalLogger
from evals.metrics import Scores, format_compliance
from evals.runner import EVAL_USER_ID, ChatRun, RagTarget, run_chat, run_summary

GraphFactory = Callable[[Any, Any, Any], CompiledStateGraph[Any, Any, Any, Any]]

SuiteName = Literal["smoke", "full"]
RUNS_PER_CASE: dict[str, int] = {"smoke": 1, "full": 3}  # doc 09 section 1: n=1 smoke, n=3 full
CONCURRENCY = 4
RESULTS_DIR = REPO_ROOT / "evals" / "results"

# Every metric this runner can produce. `count` metrics are totals; the rest are means unless the
# key says otherwise (ttft is a p95).
METRIC_KEYS = (
    "chat.format_compliance",
    "chat.refusal_accuracy",
    "chat.answer_relevance",
    "summary.fact_retention",
    "safety.system_prompt_leaks",
    "safety.canary_pass_rate",
    "ops.ttft_p95_ms",
    "ops.cost_per_turn_usd",
    "ops.cached_input_ratio",
    "rag.context_recall",
    "rag.search_rate",
    "rag.citation_validity",
    "rag.faithfulness",
    "rag.answer_correctness",
    "rag.answer_correctness.whole_doc",
    "rag.unanswerable_accuracy",
    "tools.selection_accuracy",
    "tools.arg_validity",
    "tools.unnecessary_call_rate",
    "tools.sequence_accuracy",
    "tools.approval_enforced",
    "memory.extract_precision",
    "memory.extract_recall",
    "memory.sensitive_refusal",
)
SUITE_DATASETS = (
    "chat.golden",
    "summary.golden",
    "perf.latency",
    "safety.redteam",
    "rag.golden",
    "tools.routing",
    "memory.extract",
)
# The metrics each dataset can produce: a metric is "not measured" only if a dataset that should
# have produced it ran. (`rag.golden` runs only when a RAG environment is given.)
METRICS_OF: dict[str, tuple[str, ...]] = {
    "chat.golden": (
        "chat.format_compliance",
        "chat.refusal_accuracy",
        "chat.answer_relevance",
        "ops.cost_per_turn_usd",
    ),
    "summary.golden": ("summary.fact_retention",),
    "perf.latency": ("ops.ttft_p95_ms", "ops.cost_per_turn_usd", "ops.cached_input_ratio"),
    "safety.redteam": (
        "chat.refusal_accuracy",
        "safety.system_prompt_leaks",
        "safety.canary_pass_rate",
    ),
    "rag.golden": tuple(k for k in METRIC_KEYS if k.startswith("rag.")),
    "tools.routing": tuple(k for k in METRIC_KEYS if k.startswith("tools.")),
    "memory.extract": tuple(k for k in METRIC_KEYS if k.startswith("memory.")),
}


@dataclass
class CaseRecord:
    dataset: str
    case_id: str
    run: int
    output: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "case": self.case_id,
            "run": self.run,
            "error": self.error,
            "scores": self.scores,
            "notes": self.notes,
            "output": self.output,
        }


@dataclass
class Report:
    metrics: dict[str, float]
    run: dict[str, Any]
    cases: list[CaseRecord]

    def results_json(self) -> dict[str, Any]:
        return {"metrics": self.metrics, "run": self.run}


class SuiteRun:
    """What a suite run uses, so tests can swap the pieces."""

    def __init__(
        self,
        *,
        settings: Settings,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        summarizer: Any,
        judges: Judges | None,
        logger: EvalLogger,
        runs: int,
        concurrency: int = CONCURRENCY,
    ) -> None:
        self.settings = settings
        self.graph = graph
        self.summarizer = summarizer
        self.judges = judges
        self.logger = logger
        self.runs = runs
        self.scores = Scores()
        self.records: list[CaseRecord] = []
        self.errors = 0
        self.blocked = 0  # runs the provider rejected as unsafe before any answer
        self.gate = asyncio.Semaphore(concurrency)
        self.system_prompt = load_prompt("nexus.system").body

    async def chat(
        self,
        text: str,
        history: Sequence[Any] = (),
        *,
        graph: CompiledStateGraph[Any, Any, Any, Any] | None = None,
        user_id: str = EVAL_USER_ID,
    ) -> ChatRun:
        async with self.gate:
            return await run_chat(
                graph or self.graph,
                default_model=self.settings.llm_chat_model,
                text=text,
                history=history,
                user_id=user_id,
            )

    def record(self, record: CaseRecord, *, case_input: Any) -> None:
        self.records.append(record)
        if record.error:
            self.errors += 1
        self.logger.log_case(
            record.dataset,
            record.case_id,
            case_input=case_input,
            output=record.output,
            scores=record.scores,
            metadata={"run": record.run, "error": record.error},
        )


def _turn_costs(suite: SuiteRun, result: ChatRun) -> None:
    if result.error is None:
        suite.scores.add("ops.cost_per_turn_usd", float(result.cost_usd))


async def _each(
    cases: Sequence[Any], runs: int, work: Callable[[Any, int], Awaitable[None]], *, parallel: bool
) -> None:
    jobs = [work(case, i) for case in cases for i in range(runs)]
    if parallel:
        await asyncio.gather(*jobs)
    else:
        for job in jobs:
            await job


async def run_chat_golden(suite: SuiteRun, dataset: Dataset[ChatCase]) -> None:
    async def work(case: ChatCase, index: int) -> None:
        result = await suite.chat(case.input)
        record = CaseRecord("chat.golden", case.id, index, result.output, error=result.error)
        _turn_costs(suite, result)
        if result.blocked:
            suite.blocked += 1
            record.notes["provider_blocked"] = True
            if case.should_refuse:
                # The provider refused before the model could: the harm did not get through, so
                # the case counts as refused. (Product side: the user sees an error, not a refusal.)
                record.error = None
                record.scores["chat.refusal_accuracy"] = 1.0
                suite.scores.add("chat.refusal_accuracy", 1.0)
                suite.record(record, case_input=case.input)
                return
        if case.expect.must_include or case.expect.must_not_include:
            # A run that errored counts as a failed check: an outage must not raise the metric.
            ok = result.error is None and format_compliance(
                result.output,
                must_include=case.expect.must_include,
                must_not_include=case.expect.must_not_include,
            )
            record.scores["chat.format_compliance"] = float(ok)
        if result.error is None and suite.judges is not None:
            refused = await suite.judges.refused(case.input, result.output)
            if refused is not None:
                record.scores["chat.refusal_accuracy"] = float(refused == case.should_refuse)
            if not case.should_refuse:
                relevance = await suite.judges.relevance(case.input, result.output)
                if relevance is not None:
                    record.scores["chat.answer_relevance"] = relevance
        for key, value in record.scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.input)

    await _each(dataset.cases, suite.runs, work, parallel=True)


async def run_perf(suite: SuiteRun, dataset: Dataset[PerfCase]) -> None:
    """One at a time: concurrent calls would inflate the time to first token."""

    async def work(case: PerfCase, index: int) -> None:
        result = await suite.chat(case.input, case.history)
        record = CaseRecord("perf.latency", case.id, index, result.output, error=result.error)
        _turn_costs(suite, result)
        if result.error is None:
            if not case.uses_tools and result.ttft_ms is not None:
                suite.scores.add("ops.ttft_p95_ms", result.ttft_ms)
                record.notes["ttft_ms"] = round(result.ttft_ms, 1)
            if case.history and result.usage.prompt_tokens > 0:
                ratio = result.usage.cached_tokens / result.usage.prompt_tokens
                suite.scores.add("ops.cached_input_ratio", ratio)
                record.scores["ops.cached_input_ratio"] = ratio
            record.notes["cost_usd"] = float(result.cost_usd)
        suite.record(record, case_input=case.input)

    await _each(dataset.cases, suite.runs, work, parallel=False)


async def run_redteam(suite: SuiteRun, dataset: Dataset[RedTeamCase]) -> None:
    async def work(case: RedTeamCase, index: int) -> None:
        result = await suite.chat(case.input)
        record = CaseRecord("safety.redteam", case.id, index, result.output, error=result.error)
        record.notes["threat_id"] = case.threat_id
        if result.blocked:
            suite.blocked += 1
            record.notes["provider_blocked"] = True
            if case.should_refuse:
                record.error = None
                record.scores["chat.refusal_accuracy"] = 1.0
                record.scores["safety.canary_pass_rate"] = 1.0
                suite.scores.add("chat.refusal_accuracy", 1.0)
                suite.scores.add("safety.canary_pass_rate", 1.0)
                suite.record(record, case_input=case.input)
                return
        if result.error is None:
            verdict = check_canaries(
                result.output, case.canaries, system_prompt=suite.system_prompt
            )
            record.notes["violations"] = verdict.violations
            record.scores["safety.canary_pass_rate"] = float(verdict.passed)
            if case.canaries.system_prompt_leak:
                record.scores["safety.system_prompt_leaks"] = float(verdict.prompt_leaked)
            if case.should_refuse and suite.judges is not None:
                refused = await suite.judges.refused(case.input, result.output)
                if refused is not None:
                    record.scores["chat.refusal_accuracy"] = float(refused)
        for key, value in record.scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.input)

    await _each(dataset.cases, suite.runs, work, parallel=True)


async def run_summaries(suite: SuiteRun, dataset: Dataset[SummaryCase]) -> None:
    async def work(case: SummaryCase, index: int) -> None:
        async with suite.gate:
            summary = await run_summary(suite.summarizer, case.messages)
        record = CaseRecord("summary.golden", case.id, index, summary or "")
        if summary is None:
            record.error = "NoSummary"
        elif suite.judges is not None:
            kept = await suite.judges.fact_retention(summary, case.key_facts)
            if kept is not None:
                record.scores["summary.fact_retention"] = kept
                suite.scores.add("summary.fact_retention", kept)
        suite.record(record, case_input=[t.model_dump() for t in case.messages])

    await _each(dataset.cases, suite.runs, work, parallel=True)


def _metrics(scores: Scores, ran: Sequence[str]) -> tuple[dict[str, float], list[str]]:
    values: dict[str, float | None] = {
        "chat.format_compliance": scores.mean("chat.format_compliance"),
        "chat.refusal_accuracy": scores.mean("chat.refusal_accuracy"),
        "chat.answer_relevance": scores.mean("chat.answer_relevance"),
        "summary.fact_retention": scores.mean("summary.fact_retention"),
        # A count, not a mean: one leak anywhere must show.
        "safety.system_prompt_leaks": scores.total("safety.system_prompt_leaks"),
        "safety.canary_pass_rate": scores.mean("safety.canary_pass_rate"),
        "ops.ttft_p95_ms": scores.percentile("ops.ttft_p95_ms", 95),
        "ops.cost_per_turn_usd": scores.mean("ops.cost_per_turn_usd"),
        "ops.cached_input_ratio": scores.mean("ops.cached_input_ratio"),
    }
    values.update(
        {k: scores.mean(k) for k in METRIC_KEYS if k.startswith(("rag.", "tools.", "memory."))}
    )
    metrics = {k: round(v, 6) for k, v in values.items() if v is not None}
    expected = {key for name in ran for key in METRICS_OF[name]}
    skipped = [k for k in METRIC_KEYS if k in expected and k not in metrics]
    return metrics, skipped


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return out.stdout.strip() or None


async def run_suite(
    suite: SuiteName,
    *,
    settings: Settings,
    graph: CompiledStateGraph[Any, Any, Any, Any],
    summarizer: Any,
    judges: Judges | None,
    logger: EvalLogger,
    runs: int | None = None,
    only: Sequence[str] | None = None,
    datasets_dir: Path | None = None,
    concurrency: int = CONCURRENCY,
    rag: RagTarget | None = None,
    tools_graph: GraphFactory | None = None,
    memory_model: Runnable[Any, Any] | None = None,
) -> Report:
    n = runs if runs is not None else RUNS_PER_CASE[suite]
    smoke_only = suite == "smoke"
    selected = [
        name
        for name in SUITE_DATASETS
        if (only is None or name in only)
        and (name != "rag.golden" or rag is not None)
        and (name != "tools.routing" or tools_graph is not None)
        and (name != "memory.extract" or memory_model is not None)
    ]
    chat = load_dataset("chat.golden", ChatCase, smoke_only=smoke_only, directory=datasets_dir)
    perf = load_dataset("perf.latency", PerfCase, smoke_only=smoke_only, directory=datasets_dir)
    red = load_dataset("safety.redteam", RedTeamCase, smoke_only=smoke_only, directory=datasets_dir)
    summaries = load_dataset(
        "summary.golden", SummaryCase, smoke_only=smoke_only, directory=datasets_dir
    )
    loaded: dict[str, Dataset[Any]] = {
        "chat.golden": chat,
        "summary.golden": summaries,
        "perf.latency": perf,
        "safety.redteam": red,
    }
    if rag is not None:
        loaded["rag.golden"] = load_dataset(
            "rag.golden", RagCase, smoke_only=smoke_only, directory=datasets_dir
        )

    if tools_graph is not None:
        loaded["tools.routing"] = load_dataset(
            "tools.routing", ToolsCase, smoke_only=smoke_only, directory=datasets_dir
        )

    if memory_model is not None:
        loaded["memory.extract"] = load_dataset(
            "memory.extract", MemoryExtractCase, smoke_only=smoke_only, directory=datasets_dir
        )

    started = time.perf_counter()
    started_at = datetime.now(UTC)
    state = SuiteRun(
        settings=settings,
        graph=graph,
        summarizer=summarizer,
        judges=judges,
        logger=logger,
        runs=n,
        concurrency=concurrency,
    )
    for name in selected:
        data = loaded[name]
        logger.sync_dataset(
            name,
            data.version,
            [(c.id, _input_of(c), None, {"smoke": c.smoke}) for c in data.cases],
        )

    steps: dict[str, Callable[[], Awaitable[None]]] = {
        "chat.golden": lambda: run_chat_golden(state, chat),
        "summary.golden": lambda: run_summaries(state, summaries),
        "safety.redteam": lambda: run_redteam(state, red),
        "perf.latency": lambda: run_perf(state, perf),  # last: nothing else competes for the API
    }
    if rag is not None:
        from evals.rag import run_rag_golden  # here: evals.rag imports this module

        rag_cases = loaded["rag.golden"]
        steps["rag.golden"] = lambda: run_rag_golden(
            state, rag_cases, graph=rag.graph, user_id=rag.user_id
        )
    if tools_graph is not None:
        from evals.tools import run_tools_routing  # here: evals.tools imports this module

        tools_cases = loaded["tools.routing"]
        steps["tools.routing"] = lambda: run_tools_routing(
            state, tools_cases, graph_factory=tools_graph
        )
    if memory_model is not None:
        from evals.memory import run_memory_extract  # here: evals.memory imports this module

        memory_cases = loaded["memory.extract"]
        steps["memory.extract"] = lambda: run_memory_extract(
            state, memory_cases, model=memory_model
        )
    # rag.golden before perf.latency: latency runs last, with nothing else using the API.
    order = [n for n in selected if n != "perf.latency"] + [
        n for n in selected if n == "perf.latency"
    ]
    for name in order:
        await steps[name]()
    logger.flush()

    metrics, skipped = _metrics(state.scores, selected)
    run = {
        "suite": suite,
        "mode": settings.llm_mode,
        "runs_per_case": n,
        "started_at": started_at.isoformat(timespec="seconds"),
        "duration_s": round(time.perf_counter() - started, 1),
        "git_sha": _git_sha(),
        "models": {
            "chat": settings.llm_chat_model,
            "chat_effort": settings.llm_chat_reasoning_effort,
            "background": settings.llm_background_model,
            "judge": settings.llm_judge_model if judges is not None else None,
        },
        "prompt_versions": {p.name: p.version for p in all_prompts()},
        "datasets": {
            name: {"version": loaded[name].version, "cases": len(loaded[name].cases)}
            for name in selected
        },
        "case_runs": len(state.records),
        "errors": state.errors,
        "provider_blocked": state.blocked,
        "judge_calls": judges.calls if judges else 0,
        "judge_failures": judges.failures if judges else 0,
        "skipped_metrics": skipped,
    }
    return Report(metrics=metrics, run=run, cases=state.records)


def _input_of(case: Any) -> Any:
    if isinstance(case, SummaryCase):
        return [t.model_dump() for t in case.messages]
    if isinstance(case, MemoryExtractCase):
        return case.user_message
    return case.input


def write_report(report: Report, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.results_json(), indent=2, sort_keys=True) + "\n")
    cases = path.with_suffix(".cases.json")
    cases.write_text(json.dumps([c.as_json() for c in report.cases], indent=2) + "\n")
    return cases


def default_output(settings: Settings) -> Path:
    # A fake run must never be mistaken for a measurement of the real model.
    return RESULTS_DIR / ("latest.fake.json" if settings.llm_mode == "fake" else "latest.json")


def _summary(report: Report) -> str:
    run = report.run
    lines = [f"eval {run['suite']} ({run['mode']}): {run['case_runs']} case runs"]
    lines += [f"  {key}: {value:g}" for key, value in sorted(report.metrics.items())]
    if run["skipped_metrics"]:
        lines.append("  not measured: " + ", ".join(run["skipped_metrics"]))
    if run["errors"]:
        lines.append(f"  {run['errors']} case run(s) failed (see the cases file)")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    fake = settings.llm_mode == "fake"
    try:
        graph = build_graph(checkpointer=None, settings=settings)
        summarizer = get_model("summarize", settings=settings)
        judges = None if fake else Judges(get_model("judge", settings=settings))
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rag: RagTarget | None = None
    rag_env: RagEnv | None = None
    wants_rag = not args.no_rag and (not args.dataset or "rag.golden" in args.dataset)
    if wants_rag:
        try:
            rag_env = await ingest_corpus(settings)
        except (RagSetupError, LLMConfigError) as exc:
            print(
                f"error: {exc} (is Qdrant running? use --no-rag to skip rag.golden)",
                file=sys.stderr,
            )
            return 2
        rag = RagTarget(
            graph=build_graph(
                checkpointer=None,
                settings=settings,
                retriever=rag_env.retriever,
                recent_files=rag_env.recent_files,
            ),
            user_id=rag_env.user_id,
        )

    def tools_graph(retriever: Any, web: Any, recent_files: Any) -> Any:
        # Stand-in tools (no network, no Qdrant); the model, the prompt and the graph are real.
        return build_graph(
            checkpointer=InMemorySaver(),
            settings=settings,
            retriever=retriever,
            web_searcher=web,
            calculator=True,
            recent_files=recent_files,
        )

    memory_model = (
        get_model("memory_extract", settings=settings)
        if not args.dataset or "memory.extract" in args.dataset
        else None
    )
    tracer = None if fake or args.no_langfuse else Tracer(settings)
    run_name = f"{args.suite}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    logger = EvalLogger(
        tracer.client if tracer else None, run_name=run_name, environment=settings.app_env
    )
    try:
        report = await run_suite(
            args.suite,
            settings=settings,
            graph=graph,
            summarizer=summarizer,
            judges=judges,
            logger=logger,
            runs=args.runs,
            only=args.dataset or None,
            rag=rag,
            tools_graph=tools_graph,
            memory_model=memory_model,
        )
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if tracer is not None:
            await asyncio.to_thread(tracer.shutdown)
        if rag_env is not None:
            await rag_env.close()

    out = args.out or default_output(settings)
    write_report(report, out)
    print(_summary(report))
    print(f"wrote {out}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Nexus AI eval suite")
    parser.add_argument("--suite", choices=["smoke", "full"], required=True)
    parser.add_argument("--runs", type=int, help="runs per case (default: 1 smoke, 3 full)")
    parser.add_argument(
        "--out", type=Path, help="results file (default: evals/results/latest.json)"
    )
    parser.add_argument(
        "--dataset", action="append", choices=SUITE_DATASETS, help="only this dataset (repeatable)"
    )
    parser.add_argument("--no-langfuse", action="store_true", help="do not log to Langfuse")
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="skip rag.golden (it ingests the fixture corpus, so it needs Qdrant)",
    )
    args = parser.parse_args(argv)
    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
