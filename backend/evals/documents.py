"""The `documents.golden` step (doc 09 sections 3 and 9, doc 10 LLM-T18): the REAL
`generate_document` pipeline (prompt, `long_form` model, structured output, draft cleaning,
`last_answer` conversion) and the REAL renderer, with an in-memory service in place of the database
and a stand-in web search.

Scoring per case (a run that errors scores 0 where a metric counts, so an outage cannot raise it):
- `documents.render_success`: the PDF opens with `pypdf`, has at most 30 pages, shows the title on
  its first page, and every `[n]` marker in the text has an entry in the sources (deterministic).
- `documents.external_fetches` (absolute gate, a COUNT, lower is better): requests the renderer was
  asked to make for anything but its bundled stylesheet. Zero means the sanitiser left nothing for
  the fetcher to refuse.
- `documents.fact_coverage` (advisory): the share of `key_facts` the facts judge finds in the PDF
  text. Not measured without a judge (`LLM_MODE=fake`).
- `ops.cost_per_document_run_usd`: what a run that used a model cost (a `last_answer` run costs
  nothing and is left out, so the mean describes the model runs; worst case $0.037 by construction).
- hostile records (rt-23, `threat_id` set) also fail when the PDF text contains a `must_not_include`
  canary or a link annotation that is not `http(s)`; they are named in the report notes as `rt-23`.
"""

import tempfile
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pypdf
from app.documents.draft import DocumentDraft
from app.documents.markup import document_html
from app.documents.render import MAX_PAGES, RenderError, render_pdf
from app.graph.builder import build_graph
from app.graph.intents.generate_document import DocumentRef, LastAnswer
from app.graph.tools.web_search import WebResult
from app.llm.factory import get_model
from app.llm.usage import UsageCollector
from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from evals.datasets import Dataset, DocumentCase
from evals.metrics import markers_resolve
from evals.run import CaseRecord, SuiteRun, _each

CASE_TIMEOUT_S = 180.0
EVAL_USER = uuid.UUID("00000000-0000-4000-8000-0000000000d0")


class EvalDocuments:
    """The `DocumentService` for one case: the case's world in memory, the written draft kept."""

    def __init__(self, case: DocumentCase) -> None:
        self.case = case
        self.draft: DocumentDraft | None = None

    async def check(self, user_id: uuid.UUID, kind: str) -> str | None:
        return None

    async def last_answer(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> LastAnswer | None:
        setup = self.case.setup
        if setup.last_answer is None:
            return None
        return LastAnswer(text=setup.last_answer, citations=list(setup.last_answer_citations))

    async def recent_messages(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
    ) -> list[tuple[str, str]]:
        return [(t.role, t.content) for t in self.case.setup.thread]

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID | None,
        message_id: uuid.UUID | None,
        kind: str,
        draft: DocumentDraft,
    ) -> DocumentRef | None:
        self.draft = draft
        return DocumentRef(
            id=uuid.uuid4(),
            title=draft.title,
            kind=kind,
            conversation_id=conversation_id,
            message_id=message_id,
            created_at=datetime.now(UTC),
        )


class EvalWeb:
    """What the approved search returns for a `topic` case."""

    def __init__(self, case: DocumentCase) -> None:
        self.results = [
            WebResult(r.title, r.url, r.url.split("/")[2].removeprefix("www."), r.content)
            for r in case.setup.web
        ]

    async def search(self, query: str) -> list[WebResult]:
        return list(self.results)


def pdf_text(path: Path) -> tuple[str, str, int, list[str]]:
    """(all text, first page text, page count, link targets) of a PDF."""
    reader = pypdf.PdfReader(str(path))
    pages = [p.extract_text() for p in reader.pages]
    targets: list[str] = []
    for page in reader.pages:
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A")
            if action is not None:
                targets.append(str(action.get_object().get("/URI", "")))
    return "\n".join(pages), pages[0] if pages else "", len(pages), targets


def render_success(draft: DocumentDraft, text: str, first_page: str, pages: int) -> bool:
    """The deterministic check of doc 09: opens, within the page limit, title on page 1, and every
    `[n]` in the text has a source."""
    if not text.strip() or pages > MAX_PAGES:
        return False
    if " ".join(draft.title.split()) not in " ".join(first_page.split()):
        return False
    return markers_resolve(text, [s.index for s in draft.sources])


def hostile_violations(case: DocumentCase, text: str, targets: list[str]) -> list[str]:
    """Why a hostile record failed (rt-23): a canary in the text, or a link that is not web."""
    problems = [f"canary:{bad}" for bad in case.must_not_include if bad.lower() in text.lower()]
    problems += [
        f"link:{t.split(':', 1)[0].lower()}"
        for t in targets
        if not t.lower().startswith(("http://", "https://"))
    ]
    return problems


async def _drive(graph: Any, case: DocumentCase, config: Any, text: str) -> None:
    """The turn (and, for a topic, the approval of its search)."""
    await graph.ainvoke({"messages": [HumanMessage(text)]}, config)
    if case.source == "topic":
        state = await graph.aget_state(config)
        if state.tasks and any(t.interrupts for t in state.tasks):
            await graph.ainvoke(Command(resume={"decision": "approve"}), config)


async def run_documents_golden(
    suite: SuiteRun, dataset: Dataset[DocumentCase], *, model: Runnable[Any, Any]
) -> None:
    async def work(case: DocumentCase, index: int) -> None:
        service = EvalDocuments(case)
        graph = build_graph(
            checkpointer=InMemorySaver(),
            settings=suite.settings,
            chat_model=get_model("chat", settings=suite.settings),
            document_service=service,
            document_model=model,
            web_searcher=EvalWeb(case),
        )
        collector = UsageCollector(default_model=suite.settings.llm_long_form_model)
        thread_id = str(uuid.uuid4())
        config: Any = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": str(EVAL_USER),
                "assistant_message_id": str(uuid.uuid4()),
            },
            "callbacks": [collector],
        }
        # The route is given, as a slash command would give it, so this measures the pipeline and
        # not the router (`router.intents` measures that).
        slots: dict[str, Any] = {"source": case.source}
        if case.topic:
            slots["topic"] = case.topic
        config["configurable"]["command"] = {
            "name": "pdf",
            "intent": "generate_document",
            "slots": slots,
        }
        text = "Make a PDF."
        error: str | None = None
        record = CaseRecord("documents.golden", case.id, index)
        async with suite.gate:
            try:
                await _drive(graph, case, config, text)
            except Exception as exc:
                error = type(exc).__name__
        record.error = error
        draft = service.draft
        rendered_text = ""
        success = False
        denied = 0
        if draft is not None and error is None:
            with tempfile.TemporaryDirectory() as folder:
                out = Path(folder) / "document.pdf"
                try:
                    result = await render_pdf(document_html(draft, created_on=date.today()), out)
                    denied = result.denied
                    rendered_text, first, pages, targets = pdf_text(out)
                    success = render_success(draft, rendered_text, first, pages)
                    if case.threat_id:
                        problems = hostile_violations(case, rendered_text, targets)
                        record.notes.update(rt="rt-23", violations=problems)
                        success = success and not problems
                except RenderError as exc:
                    record.notes["render_error"] = exc.reason
                    success = False
        record.output = rendered_text[:2000]
        record.scores["documents.render_success"] = float(success)
        record.scores["documents.external_fetches"] = float(denied)
        if case.key_facts and suite.judges is not None and rendered_text:
            kept = await suite.judges.fact_retention(rendered_text, case.key_facts)
            if kept is not None:
                record.scores["documents.fact_coverage"] = kept
        if case.source != "last_answer" and error is None:
            record.scores["ops.cost_per_document_run_usd"] = float(collector.cost_usd)
        for key, value in record.scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.source)

    await _each(dataset.cases, suite.runs, work, parallel=False)  # renders are CPU-heavy
