"""`deep_research`: a question researched on the web into a cited report (doc 08 section 2.5).

Nodes, in order (every one can end the turn early with a short chat reply, and then nothing more
runs):

- `research_check` resets the per-run keys and runs the pre-check: a topic, a search provider,
  web search not switched off, and the worst-case cost of the run against the user's remaining
  budget and the global cap (plus the daily document limit when a PDF was asked for).
- `research_plan` asks the planner model for 3 to 6 web queries. It is given the topic and the date
  and nothing else: no memories, no conversation (LLM-T20). The plan is cleaned in code.
- `research_confirm` shows the plan to the user (`interrupt`, tool `research_plan`) and waits. This
  is the ONLY approval of the run: the user approves, edits the queries or rejects. It is a node of
  its own so the planner is never called twice when the run resumes, and nothing before its
  `interrupt()` has a side effect.
- `research_search` runs exactly the approved queries, in parallel (at most 6), as `web_search`
  tool events with `progress` events. No other query exists: the model never sees results before
  it writes the report, so it cannot ask for follow-up searches (doc 08: no unapproved queries).
- `research_report` writes the report with the `long_form` model, streamed into the chat, with
  `[n]` citations of the numbered results, and, when a PDF was asked for, turns that report into a
  document through the same `last_answer` path (`draft_from_answer`) and announces it with
  `document_created`. A run stopped before this node writes no report; the searches already done
  are on the cancelled message as tool calls (doc 08 section 2.3). After the PDF is queued it
  finishes whatever happens to the run.

Web results are untrusted data (LLM-T02): they reach the model only as escaped `<web_result>`
blocks. The user id and the conversation come from the trusted run config, never from model output.
Nothing the user or the sources wrote is logged.
"""

import asyncio
import html
import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

import structlog
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END
from langgraph.types import interrupt
from pydantic import BaseModel

from app.documents.draft import DraftError, draft_from_answer
from app.graph.calls import disabled_tools
from app.graph.intents.generate_document import (
    LIMIT_REACHED,
    DocumentService,
    _plain,
    _uuid_or_none,
)
from app.graph.nodes.approval import APPROVAL_TTL
from app.graph.nodes.types import ChatNode
from app.graph.state import ChatState, Citation
from app.graph.tools.outcome import ToolOutcome
from app.graph.tools.web_search import (
    MAX_QUERY_CHARS,
    WebResult,
    WebSearched,
    WebSearcher,
    web_search_phase,
)
from app.graph.tools.web_search import TOOL_NAME as WEB_SEARCH
from app.llm.prompts import load_prompt
from app.llm.tokens import count_text

_log = structlog.get_logger("nexus.research")

TOOL_NAME = "research_plan"  # the approval card's tool name (spec: PendingInterrupt.tool)
PLAN_PROMPT = "nexus.research_plan"
REPORT_PROMPT = "nexus.research_report"
MIN_PLAN_QUERIES = 3  # what the planner must produce (an edit by the user may be shorter)
MAX_QUERIES = 6  # also the cap on searches per run
MAX_RESULT_TOKENS = 16_000  # the report's material (doc 08 section 2.5)
PLAN_TIMEOUT_S = 70.0  # the planner's own limit is 60 s
REPORT_TIMEOUT_S = 130.0  # the model's own limit is 120 s
MAX_TOPIC_CHARS = 200

# What the user is told when the run ended early. Fixed texts: nothing from a source, the topic or
# an exception is echoed into them.
NO_TOPIC = "Tell me what to research, for example `/research how do solid-state batteries work`."
SEARCH_OFF = "Web search is switched off right now, so I can't do research."
SEARCH_UNAVAILABLE = "Web search isn't set up on this server, so I can't do research."
PLAN_FAILED = "I couldn't plan the research this time. Please try again."
PLAN_REJECTED = "Okay, I won't search the web, so I didn't do the research."
SEARCH_FAILED = "I couldn't search the web just now, so I didn't write the report. Try again later."
NO_RESULTS = "The web searches found nothing usable for that, so I didn't write a report."
REPORT_FAILED = "I couldn't write the report this time. Please try again."

_CITED = re.compile(r"\[(\d{1,3})\]")
_SPACE = re.compile(r"\s+")

Output = Literal["chat", "pdf"]


class ResearchChecker(Protocol):
    """The pre-check. Production: `services/research.py`."""

    async def check(self, user_id: uuid.UUID, *, pdf: bool) -> str | None:
        """None when a run may start, else the reply that refuses it."""
        ...


class ResearchPlan(BaseModel):
    """The planner's structured output. No length rules here: a strict JSON schema cannot carry
    them, so `clean_queries` enforces them in code."""

    queries: list[str]


class PlanError(Exception):
    """The planner's answer cannot be used."""


def clean_queries(raw: object, *, minimum: int, cut: bool = False) -> list[str]:
    """Queries made safe to show and to search: one line each, at most 400 characters, no
    repeats (case-insensitive), at most 6. Raises `PlanError` for anything else, or when fewer than
    `minimum` are left. Used for the planner's output (minimum 3, `cut`: a planner that wrote more
    than 6 loses the extra ones) and for the user's edit (1, never cut: it is refused)."""
    if not isinstance(raw, list):
        raise PlanError("not a list")
    if len(raw) > MAX_QUERIES and not cut:
        raise PlanError("too many queries")  # the spec's `maxItems`, before any de-duplication
    seen: set[str] = set()
    queries: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise PlanError("not text")
        query = _SPACE.sub(" ", item).strip()
        if not query:
            continue
        if len(query) > MAX_QUERY_CHARS:
            raise PlanError("a query is too long")
        if query.casefold() in seen:
            continue
        seen.add(query.casefold())
        queries.append(query)
    if len(queries) > MAX_QUERIES:
        if not cut:
            raise PlanError("too many queries")
        queries = queries[:MAX_QUERIES]
    if len(queries) < minimum:
        raise PlanError("too few queries")
    return queries


def read_plan_decision(answer: object, proposed: list[str]) -> list[str] | None:
    """The queries the user approved, or None (nothing is searched). Only a clear `approve` or a
    valid `edit` counts; anything else is a rejection (fail closed)."""
    if not isinstance(answer, dict):
        return None
    if answer.get("decision") == "approve":
        return list(proposed)
    if answer.get("decision") == "edit":
        args = answer.get("args")
        try:
            return clean_queries(args.get("queries") if isinstance(args, dict) else None, minimum=1)
        except PlanError:
            return None
    return None


def _user_id(config: RunnableConfig) -> uuid.UUID:
    return uuid.UUID(str(config["configurable"]["user_id"]))  # trusted run config


def _slots(state: ChatState) -> dict[str, Any]:
    return (state.get("route") or {}).get("slots") or {}


def _topic(state: ChatState) -> str:
    return str(_slots(state).get("topic") or "").strip()[:MAX_TOPIC_CHARS]


def _stop(text: str) -> dict[str, Any]:
    return {"messages": [AIMessage(content=text)], "research_stop": True}


def after(next_node: str) -> Callable[[ChatState], str]:
    """The conditional edge of a straight line: on to `next_node`, or the end when a node stopped
    the turn."""

    def route(state: ChatState) -> str:
        return END if state.get("research_stop") else next_node

    return route


def make_check_node(
    checker: ResearchChecker, searcher: WebSearcher | None, *, documents: bool
) -> ChatNode:
    async def research_check(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        # Every run starts clean: the checkpoint keeps these keys from the previous turn.
        fresh: dict[str, Any] = {
            "research_stop": False,
            "research_queries": [],
            "research_approved": [],
            "research_material": "",
            "research_sources": [],
        }
        if not _topic(state):
            return {**fresh, **_stop(NO_TOPIC)}
        if searcher is None:
            return {**fresh, **_stop(SEARCH_UNAVAILABLE)}
        if WEB_SEARCH in disabled_tools(config):
            return {**fresh, **_stop(SEARCH_OFF)}
        pdf = documents and _slots(state).get("output") == "pdf"
        refusal = await checker.check(_user_id(config), pdf=pdf)
        if refusal is not None:
            return {**fresh, **_stop(refusal)}
        return fresh

    return research_check


def make_plan_node(model: Runnable[Any, Any]) -> ChatNode:
    async def research_plan(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        _progress("planning", None, None)
        # The topic and the date, nothing else (LLM-T20): no memories, no thread text.
        prompt = load_prompt(PLAN_PROMPT).render(
            today=date.today().isoformat(), topic=html.escape(_topic(state), quote=False)
        )
        structured = model.with_structured_output(ResearchPlan, method="json_schema", strict=True)  # type: ignore[attr-defined]
        try:
            raw = await asyncio.wait_for(structured.ainvoke(prompt, config), PLAN_TIMEOUT_S)
            plan = raw if isinstance(raw, ResearchPlan) else ResearchPlan.model_validate(raw)
            queries = clean_queries(plan.queries, minimum=MIN_PLAN_QUERIES, cut=True)
        except Exception as exc:  # any failure is the same reply; the log carries the type only
            _log.warning("research_plan_failed", error_type=type(exc).__name__)
            return _stop(PLAN_FAILED)
        return {"research_queries": queries}

    return research_plan


def make_confirm_node() -> ChatNode:
    async def research_confirm(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        proposed = list(state.get("research_queries") or [])
        now = datetime.now(UTC)
        # Nothing before this line has a side effect: the node runs again from the top on resume.
        answer = interrupt(
            {
                "interrupt_id": f"int_{uuid.uuid4().hex[:12]}",
                "tool": TOOL_NAME,
                "tool_call_id": f"plan_{uuid.uuid4().hex[:12]}",
                "args": {"topic": _topic(state), "queries": proposed},
                "created_at": now.isoformat(),
                "expires_at": (now + APPROVAL_TTL).isoformat(),
            }
        )
        approved = read_plan_decision(answer, proposed)
        if not approved:
            return _stop(PLAN_REJECTED)
        # What the user approved, and the only thing `research_search` reads.
        return {"research_approved": approved}

    return research_confirm


def _progress(stage: str, done: int | None, total: int | None) -> None:
    get_stream_writer()({"kind": "progress", "stage": stage, "done": done, "total": total})


async def _search_one(
    searcher: WebSearcher, query: str
) -> tuple[WebSearched | None, str, str, str]:
    """(results or None, tool status, summary, call id) of one approved query. Every failure is a
    result the run can read; exception text never leaves this function."""
    call_id = f"call_{uuid.uuid4().hex[:16]}"
    write = get_stream_writer()
    write({"kind": "tool_start", "call_id": call_id, "name": WEB_SEARCH, "args": {"query": query}})
    try:
        found = await web_search_phase({"query": query}, searcher=searcher)
    except Exception as exc:
        _log.warning("research_search_failed", error_type=type(exc).__name__)
        found = ToolOutcome("The web search failed.", "failed", "search failed")
    if isinstance(found, WebSearched):
        status, summary = "completed", found.render(first_index=1).summary
        results: WebSearched | None = found
    else:
        status, summary, results = found.status, found.summary, None
    write(
        {
            "kind": "tool_end",
            "call_id": call_id,
            "name": WEB_SEARCH,
            "status": status,
            "summary": summary,
        }
    )
    return results, status, summary, call_id


def merge_results(found: list[WebSearched]) -> list[WebResult]:
    """The results of every query in query order, each page once (the same URL found by two
    queries is one source)."""
    seen: set[str] = set()
    merged: list[WebResult] = []
    for searched in found:
        for result in searched.results:
            if result.url not in seen:
                seen.add(result.url)
                merged.append(result)
    return merged


def numbered(results: list[WebResult]) -> ToolOutcome:
    """The results as numbered, escaped `<web_result>` blocks, cut (from the end) until they fit
    the report's token budget. Numbers start at 1 and match the citations."""
    kept = list(results)
    outcome = WebSearched(kept).render(first_index=1)
    while len(kept) > 1 and count_text(outcome.content) > MAX_RESULT_TOKENS:
        kept.pop()
        outcome = WebSearched(kept).render(first_index=1)
    return outcome


def make_search_node(searcher: WebSearcher | None) -> ChatNode:
    async def research_search(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        # ONLY what the user approved is searched: this is the whole of LLM-T20's control.
        queries = list(state.get("research_approved") or [])[:MAX_QUERIES]
        if searcher is None or not queries:
            return _stop(SEARCH_FAILED)
        total = len(queries)
        done = 0
        _progress("searching", 0, total)

        async def one(query: str) -> tuple[WebSearched | None, str, str, str]:
            nonlocal done
            outcome = await _search_one(searcher, query)
            done += 1
            _progress("searching", done, total)
            return outcome

        outcomes = await asyncio.gather(*(one(q) for q in queries))
        searched = [r for r, *_ in outcomes if r is not None]
        if not searched:
            return _stop(SEARCH_FAILED)
        merged = merge_results(searched)
        if not merged:
            return _stop(NO_RESULTS)
        rendered = numbered(merged)
        return {"research_material": rendered.content, "research_sources": rendered.citations}

    return research_search


def cited_sources(text: str, sources: list[Citation]) -> list[Citation]:
    """The sources the report cites as `[n]` (all of them when it cites none), so the chips and
    the PDF's source list match the text."""
    cited = {int(n) for n in _CITED.findall(text)}
    return [s for s in sources if s["index"] in cited] or list(sources)


def make_report_node(model: Runnable[Any, Any], documents: DocumentService | None) -> ChatNode:
    async def research_report(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        topic = _topic(state)
        _progress("writing", None, None)
        prompt = load_prompt(REPORT_PROMPT).render(
            topic=html.escape(topic, quote=False),
            today=date.today().isoformat(),
            results=state.get("research_material") or "",
        )
        try:
            reply = await asyncio.wait_for(model.ainvoke(prompt, config), REPORT_TIMEOUT_S)
        except Exception as exc:
            _log.warning("research_report_failed", error_type=type(exc).__name__)
            return _stop(REPORT_FAILED)
        text = reply.text.strip() if isinstance(reply, AIMessage) else ""
        if not text:
            return _stop(REPORT_FAILED)
        write = get_stream_writer()
        sources = cited_sources(text, list(state.get("research_sources") or []))
        for citation in sources:
            write({"kind": "citation", "citation": citation})
        footer = ""
        if documents is not None and _slots(state).get("output") == "pdf":
            footer = await _make_pdf(documents, config, topic, text, sources)
        return {
            "messages": [
                AIMessage(
                    content=text + footer,
                    response_metadata=dict(getattr(reply, "response_metadata", None) or {}),
                )
            ]
        }

    return research_report


async def _make_pdf(
    documents: DocumentService,
    config: RunnableConfig,
    topic: str,
    report: str,
    sources: list[Citation],
) -> str:
    """The report as a PDF, through the same path as `/pdf last` (no model call). Returns the
    line to add under the report; the report itself is never lost to a failure here."""
    _progress("rendering", None, None)
    try:
        draft = draft_from_answer(report, title_hint=topic, citations=sources)
    except DraftError:
        return ""
    configurable = config["configurable"]
    ref = await documents.create(
        _user_id(config),
        conversation_id=_uuid_or_none(configurable.get("thread_id")),
        message_id=_uuid_or_none(configurable.get("assistant_message_id")),
        kind="research_report",
        draft=draft,
    )
    if ref is None:
        return f"\n\n{LIMIT_REACHED}"
    get_stream_writer()({"kind": "document_created", "document": ref.as_event()})
    return f"\n\nI've made **{_plain(ref.title)}** into a PDF. It's being prepared now."
