"""The `tools.routing` step (doc 09 sections 3 and 9): does the model pick the right tool with
valid arguments, and is every web search asked about before it runs?

Each case runs through the REAL graph (approval node and all) with the real chat model, but the
tools are stand-ins: file search finds exactly the text the case lists, web search returns fixed
results and never leaves the process, so the step costs model tokens only. A web search interrupts
the run; the step answers every interrupt with "approve" and carries on, so multi-step cases
(file, then web) can finish.

Metrics: `tools.selection_accuracy` (first call is the expected tool, or no call when none is
expected), `tools.arg_validity` (the expected call's arguments validate and contain the expected
text; a case whose expected tool was never called scores 0), `tools.unnecessary_call_rate` (lower is
better: a call when none was expected), `tools.sequence_accuracy` (all calls, in order, match the
expected sequence), and `tools.approval_enforced` (a web search never ran before the user was
asked: absolute gate). The last one also comes from three scripted probes that need no model, so it
is measured on every run, including a mixed message and two searches in one message. A run that
errors counts as a miss, so an outage cannot raise a metric.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.graph.builder import build_graph
from app.graph.nodes.load_context import RecentFiles
from app.graph.state import RecentFile
from app.graph.tools.calculator import CalculatorArgs
from app.graph.tools.search_my_files import SearchMyFilesArgs
from app.graph.tools.web_search import WebResult, WebSearchArgs, WebSearcher
from app.llm.fake import FakeChatModel, FakeWebSearcher
from app.rag.retriever import Passage, Retrieved, Searcher
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

from evals.datasets import Dataset, SetupDoc, ToolsCase
from evals.run import CaseRecord, SuiteRun, _each

GraphFactory = Callable[
    [Searcher, WebSearcher, RecentFiles], CompiledStateGraph[Any, Any, Any, Any]
]

CASE_TIMEOUT_S = 120.0
MAX_APPROVALS = 8
_FILE_NS = uuid.UUID("5c1c2f5e-8d0a-4d6e-9a43-0b1e2f3a4b5c")
_ARGS: dict[str, type[BaseModel]] = {
    "search_my_files": SearchMyFilesArgs,
    "web_search": WebSearchArgs,
    "calculator": CalculatorArgs,
}
_PROBE_FILE = SetupDoc(name="q3-report.pdf", text="Monthly churn decreased from 3.8% to 3.1%.")


class FileStub:
    """File search over the case's own documents: every query finds all of them."""

    def __init__(self, docs: list[SetupDoc]) -> None:
        self.docs = docs

    async def search(self, user_id: uuid.UUID, query: str, file_ids: Any = None) -> Retrieved:
        passages = [
            Passage(
                file_id=uuid.uuid5(_FILE_NS, doc.name),
                filename=doc.name,
                page=1,
                text=doc.text,
                score=0.9,
            )
            for doc in self.docs[:5]
        ]
        return Retrieved(passages=passages, embedding_tokens=0)


class WebStub(FakeWebSearcher):
    """The offline web searcher that remembers every query it really ran."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str) -> list[WebResult]:
        self.queries.append(query)
        return await super().search(query)


def recent_files_of(docs: list[SetupDoc]) -> RecentFiles:
    today = datetime.now(UTC).isoformat(timespec="seconds")

    async def recent(user_id: uuid.UUID) -> list[RecentFile]:
        return [
            {"id": str(uuid.uuid5(_FILE_NS, d.name)), "filename": d.name, "uploaded_at": today}
            for d in docs
        ]

    return recent


@dataclass
class ToolRun:
    output: str = ""
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # in the order made
    results: list[str] = field(default_factory=list)  # what the model was shown, shortened
    interrupts: int = 0
    # Web searches that had really run each time the run stopped for an approval. Before the
    # k-th decision at most k searches may have run: a larger number is a search nobody approved.
    searches_at_pause: list[int] = field(default_factory=list)
    searches_run: int = 0
    error: str | None = None

    @property
    def search_calls(self) -> int:
        return sum(1 for name, _ in self.calls if name == "web_search")

    @property
    def approval_enforced(self) -> bool:
        asked_first = all(ran <= k for k, ran in enumerate(self.searches_at_pause))
        return asked_first and self.searches_run <= self.interrupts


async def run_one(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    web: WebStub,
    text: str,
    *,
    timeout_s: float = CASE_TIMEOUT_S,
) -> ToolRun:
    """One user turn, answering every approval with "approve". Never raises."""
    config: Any = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "user_display_name": "Eval User",
            "today": datetime.now(UTC).date().isoformat(),
        }
    }
    run = ToolRun()

    async def drive() -> None:
        await graph.ainvoke({"messages": [HumanMessage(text)]}, config)
        while True:
            state = await graph.aget_state(config)
            if not state.interrupts:
                return
            if run.interrupts >= MAX_APPROVALS:
                raise RuntimeError("too many approvals in one turn")
            run.searches_at_pause.append(len(web.queries))
            run.interrupts += len(state.interrupts)
            await graph.ainvoke(Command(resume={"decision": "approve"}), config)

    try:
        await asyncio.wait_for(drive(), timeout=timeout_s)
        messages = (await graph.aget_state(config)).values.get("messages", [])
        for message in messages:
            if isinstance(message, AIMessage):
                run.calls += [(str(c["name"]), dict(c["args"])) for c in message.tool_calls]
            elif isinstance(message, ToolMessage):
                run.results.append(message.text[:300])
        last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        run.output = last.text if last is not None else ""
    except Exception as exc:
        run.error = type(exc).__name__
    run.searches_run = len(web.queries)
    return run


def args_are_valid(name: str, args: dict[str, Any], expected: dict[str, list[str]]) -> bool:
    """The arguments validate against the tool's schema and contain every expected text in the
    named argument (case-insensitive)."""
    try:
        _ARGS[name].model_validate(args)
    except ValidationError:
        return False
    return all(
        all(needle.casefold() in str(args.get(key, "")).casefold() for needle in needles)
        for key, needles in expected.items()
    )


async def approval_probes(suite: SuiteRun) -> None:
    """Three scripted runs that need no model: one search, a message that mixes file search and web
    search, and two searches in one message. Each scores 1 only if no search ran before its
    approval AND the approved searches really ran, so a wiring break in either direction shows."""
    probes = [
        ("[[fixture:web_search]] probe", 1),
        ("[[fixture:mixed_search]] probe", 1),
        ("[[fixture:two_searches]] probe", 2),
    ]
    for text, expected_searches in probes:
        web = WebStub()
        graph = build_graph(
            checkpointer=InMemorySaver(),
            chat_model=FakeChatModel(role="chat"),
            summarize_model=FakeChatModel(role="summarize"),
            retriever=FileStub([_PROBE_FILE]),
            web_searcher=web,
            calculator=True,
        )
        run = await run_one(graph, web, text)
        ok = run.error is None and run.approval_enforced and run.searches_run == expected_searches
        suite.scores.add("tools.approval_enforced", ok)


async def run_tools_routing(
    suite: SuiteRun, dataset: Dataset[ToolsCase], *, graph_factory: GraphFactory
) -> None:
    await approval_probes(suite)

    async def work(case: ToolsCase, index: int) -> None:
        web = WebStub()
        async with suite.gate:
            try:
                graph = graph_factory(
                    FileStub(case.setup.docs), web, recent_files_of(case.setup.docs)
                )
            except Exception as exc:
                run = ToolRun(error=type(exc).__name__)
            else:
                run = await run_one(graph, web, case.input)
        record = CaseRecord("tools.routing", case.id, index, run.output, error=run.error)
        names = [name for name, _ in run.calls]
        first = run.calls[0] if run.calls else None
        record.notes.update(
            calls=names,
            interrupts=run.interrupts,
            first_args=first[1] if first else None,
            results=run.results,
            expected_tool=case.expected_tool,
        )
        scores = record.scores
        failed = run.error is not None

        scores["tools.selection_accuracy"] = float(
            not failed and (first[0] if first else None) == case.expected_tool
        )
        if case.expected_tool is None:
            scores["tools.unnecessary_call_rate"] = float(not failed and bool(names))
        else:
            called = next((a for n, a in run.calls if n == case.expected_tool), None)
            scores["tools.arg_validity"] = float(
                not failed
                and called is not None
                and args_are_valid(case.expected_tool, called, case.expected_args_contains)
            )
        if case.expected_sequence is not None:
            scores["tools.sequence_accuracy"] = float(
                not failed and names == case.expected_sequence
            )
        if run.search_calls:
            scores["tools.approval_enforced"] = float(run.approval_enforced)
        if case.expect_interrupt:
            record.notes["interrupted"] = run.interrupts > 0

        for key, value in scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.input)

    await _each(dataset.cases, suite.runs, work, parallel=True)
