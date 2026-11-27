"""`generate_document`: a conversation, the last answer or a topic as a PDF (doc 08 section 2.5).

Nodes, in order (every one can end the turn early with a short chat reply, and then nothing is
written):

- `document_check` resets the per-run keys and runs the pre-check: the daily limit and the
  worst-case cost of the run against the user's remaining budget and the global cap. A refusal is a
  chat message and nothing runs.
- `document_gather` for `thread` (the checkpoint summary plus the newest stored messages, <= 16k
  tokens, as data) and `last_answer` (the previous answer converted with NO model call).
- `document_search` for `topic`: one `web_search`, approved by the user first (the same card as in
  chat; edit and reject work), then run once. Only the approved query leaves the system.
- `document_compose` writes a `DocumentDraft` with the `long_form` model (strict structured output,
  max output 4k). Sources are never taken from the model: a brief lists the search results it cited,
  a thread has none.
- `document_persist` writes the `pending` row, queues `render_document` and announces it with
  `document_created`. After this call the document finishes whatever happens to the run (doc 08
  section 2.3); a run stopped earlier creates nothing.

LangGraph runs a node again from its top when it resumes, so `document_search` has no side effect
before its `interrupt()`. The user id and the conversation come from the trusted run config, never
from model output. Nothing the user or the sources wrote is logged.
"""

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

import structlog
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END
from langgraph.types import interrupt

from app.documents.draft import (
    DocumentDraft,
    DraftError,
    Source,
    clean_draft,
    draft_from_answer,
    thread_material,
)
from app.graph.calls import disabled_tools
from app.graph.nodes.approval import APPROVAL_TTL, read_decision
from app.graph.nodes.types import ChatNode
from app.graph.state import ChatState, Citation
from app.graph.tools.outcome import ToolOutcome
from app.graph.tools.web_search import (
    TOOL_NAME as WEB_SEARCH,
)
from app.graph.tools.web_search import (
    WebSearched,
    WebSearcher,
    web_search_phase,
)
from app.llm.prompts import load_prompt

_log = structlog.get_logger("nexus.documents")

PROMPT_NAME = "nexus.document"
COMPOSE_TIMEOUT_S = 130.0  # the model's own limit is 120 s
THREAD_MESSAGE_LIMIT = 200  # newest stored messages read; the token budget cuts further

Source_ = Literal["thread", "last_answer", "topic"]
Kind = Literal["thread_summary", "answer", "news_brief"]
KIND_OF: dict[str, Kind] = {
    "thread": "thread_summary",
    "last_answer": "answer",
    "topic": "news_brief",
}

# What the user is told when nothing was made. Fixed texts: nothing from the material or from an
# exception is echoed into them.
NOTHING_YET = "There is nothing in this conversation yet to turn into a PDF."
NO_ANSWER = "I don't have an earlier answer to turn into a PDF yet."
SEARCH_REJECTED = "Okay, I won't search the web, so I didn't write the brief."
SEARCH_FAILED = "I couldn't search the web just now, so I didn't write the brief. Try again later."
SEARCH_OFF = "Web search is switched off right now, so I can't write a brief."
SEARCH_UNAVAILABLE = "Web search isn't set up on this server, so I can't write a brief."
COMPOSE_FAILED = "I couldn't write the document this time. Please try again."
LIMIT_REACHED = "I couldn't make the document: the daily document limit is reached."
_CITED = re.compile(r"\[(\d{1,3})\]")


@dataclass(frozen=True)
class LastAnswer:
    text: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    title_hint: str | None = None  # the conversation's title, when it has one


@dataclass(frozen=True)
class DocumentRef:
    """The new document as the API's `DocumentObject` (status `pending`)."""

    id: uuid.UUID
    title: str
    kind: str
    conversation_id: uuid.UUID | None
    message_id: uuid.UUID | None
    created_at: datetime

    def as_event(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "title": self.title,
            "kind": self.kind,
            "status": "pending",
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "message_id": str(self.message_id) if self.message_id else None,
            "page_count": None,
            "size_bytes": None,
            "error": None,
            "created_at": self.created_at.isoformat(),
        }


class DocumentService(Protocol):
    """What the pipeline may read and write. Every call takes the run's trusted user id.
    Production: `services/documents.py`."""

    async def check(self, user_id: uuid.UUID, kind: str) -> str | None:
        """None when a document of this kind may start, else the reply that refuses it."""
        ...

    async def last_answer(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> LastAnswer | None: ...

    async def recent_messages(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
    ) -> list[tuple[str, str]]: ...

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID | None,
        message_id: uuid.UUID | None,
        kind: str,
        draft: DocumentDraft,
    ) -> DocumentRef | None:
        """Write the row and queue the render, or None when the daily limit is reached."""
        ...


def _user_id(config: RunnableConfig) -> uuid.UUID:
    return uuid.UUID(str(config["configurable"]["user_id"]))  # trusted run config


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _slots(state: ChatState) -> dict[str, Any]:
    return (state.get("route") or {}).get("slots") or {}


def _stop(text: str) -> dict[str, Any]:
    return {"messages": [AIMessage(content=text)], "document_stop": True}


def after_check(state: ChatState) -> str:
    if state.get("document_stop"):
        return END
    return "document_search" if _slots(state).get("source") == "topic" else "document_gather"


def after_step(state: ChatState) -> str:
    return END if state.get("document_stop") else "document_compose"


def after_compose(state: ChatState) -> str:
    return END if state.get("document_stop") else "document_persist"


def make_check_node(service: DocumentService) -> ChatNode:
    async def document_check(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        # Every run starts clean: the checkpoint keeps these keys from the previous turn, and a
        # stale draft would make `document_compose` skip the model.
        fresh: dict[str, Any] = {
            "document_stop": False,
            "document_material": "",
            "document_sources": [],
            "document_draft": None,
            "document_kind": None,
        }
        source = _slots(state).get("source")
        if source not in KIND_OF:
            return {**fresh, **_stop(NO_ANSWER)}
        kind = KIND_OF[source]
        refusal = await service.check(_user_id(config), kind)
        if refusal is not None:
            return {**fresh, **_stop(refusal)}
        return {**fresh, "document_kind": kind}

    return document_check


def make_gather_node(service: DocumentService) -> ChatNode:
    async def document_gather(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        user_id = _user_id(config)
        conversation = _uuid_or_none(config["configurable"].get("thread_id"))
        if conversation is None:
            return _stop(NOTHING_YET)
        if _slots(state).get("source") == "thread":
            messages = await service.recent_messages(user_id, conversation, THREAD_MESSAGE_LIMIT)
            summary = state.get("summary")
            if not messages and not (summary and summary.strip()):
                return _stop(NOTHING_YET)
            return {"document_material": thread_material(summary, messages)}
        answer = await service.last_answer(user_id, conversation)
        if answer is None:
            return _stop(NO_ANSWER)
        try:  # no model call: the answer is the document
            draft = draft_from_answer(
                answer.text, title_hint=answer.title_hint, citations=answer.citations
            )
        except DraftError:
            return _stop(NO_ANSWER)
        return {"document_draft": draft.model_dump()}

    return document_gather


def make_search_node(searcher: WebSearcher | None) -> ChatNode:
    async def document_search(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        topic = str(_slots(state).get("topic") or "").strip()
        if not topic:
            return _stop(NO_ANSWER)
        if searcher is None:
            return _stop(SEARCH_UNAVAILABLE)
        if WEB_SEARCH in disabled_tools(config):
            return _stop(SEARCH_OFF)
        call_id = f"call_{uuid.uuid4().hex[:16]}"
        now = datetime.now(UTC)
        # Nothing before this line has a side effect: the node runs again from the top on resume.
        answer = interrupt(
            {
                "interrupt_id": f"int_{uuid.uuid4().hex[:12]}",
                "tool": WEB_SEARCH,
                "tool_call_id": call_id,
                "args": {"query": topic},
                "created_at": now.isoformat(),
                "expires_at": (now + APPROVAL_TTL).isoformat(),
            }
        )
        decision = read_decision(answer, WEB_SEARCH)  # anything unclear is a rejection
        if decision["decision"] == "reject":
            return _stop(SEARCH_REJECTED)
        query = decision["args"]["query"] if decision["decision"] == "edit" else topic
        write = get_stream_writer()
        write(
            {"kind": "tool_start", "call_id": call_id, "name": WEB_SEARCH, "args": {"query": query}}
        )
        found = await web_search_phase({"query": query}, searcher=searcher)
        outcome: ToolOutcome = (
            found.render(first_index=1) if isinstance(found, WebSearched) else found
        )
        write(
            {
                "kind": "tool_end",
                "call_id": call_id,
                "name": WEB_SEARCH,
                "status": outcome.status,
                "summary": outcome.summary,
            }
        )
        if outcome.status != "completed" or not outcome.citations:
            return _stop(SEARCH_FAILED)
        return {"document_material": outcome.content, "document_sources": outcome.citations}

    return document_search


def _sources_for(draft: DocumentDraft, found: list[Citation]) -> list[Source]:
    """The sources of a brief: the search results the text cites (all of them when it cites none).
    The model's own `sources` are discarded, so it cannot invent an address."""
    cited = {int(n) for section in draft.sections for n in _CITED.findall(section.body_md)}
    chosen = [c for c in found if c["index"] in cited] or list(found)
    return [Source(index=c["index"], title=c["title"], url=c.get("url")) for c in chosen]


def make_compose_node(model: Runnable[Any, Any]) -> ChatNode:
    async def document_compose(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        if state.get("document_draft"):
            return {}  # last_answer: already a draft
        source = str(_slots(state).get("source"))
        prompt = load_prompt(PROMPT_NAME).render(
            doc_kind={"thread": "conversation summary", "topic": "news brief"}.get(
                source, "document"
            ),
            title_hint=str(_slots(state).get("topic") or "none")[:200],
            today=date.today().isoformat(),
            material=state.get("document_material") or "",
        )
        structured = model.with_structured_output(DocumentDraft, method="json_schema", strict=True)  # type: ignore[attr-defined]
        try:
            raw = await asyncio.wait_for(structured.ainvoke(prompt, config), COMPOSE_TIMEOUT_S)
            draft = raw if isinstance(raw, DocumentDraft) else DocumentDraft.model_validate(raw)
            draft = draft.model_copy(
                update={
                    "sources": _sources_for(draft, state.get("document_sources") or [])
                    if source == "topic"
                    else []
                }
            )
            draft = clean_draft(draft, title_hint=_slots(state).get("topic"))
        except Exception as exc:  # any failure is the same reply; the log carries the type only
            _log.warning("document_compose_failed", error_type=type(exc).__name__)
            return _stop(COMPOSE_FAILED)
        return {"document_draft": draft.model_dump()}

    return document_compose


def _plain(title: str) -> str:
    """The title as plain text inside a Markdown reply: no emphasis, link or code characters."""
    return re.sub(r"[*_`\[\]<>\\]", "", title).strip() or "document"


def make_persist_node(service: DocumentService) -> ChatNode:
    async def document_persist(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        draft = DocumentDraft.model_validate(state.get("document_draft"))
        kind = str(state.get("document_kind") or KIND_OF[str(_slots(state).get("source"))])
        configurable = config["configurable"]
        ref = await service.create(
            _user_id(config),
            conversation_id=_uuid_or_none(configurable.get("thread_id")),
            message_id=_uuid_or_none(configurable.get("assistant_message_id")),
            kind=kind,
            draft=draft,
        )
        if ref is None:
            return _stop(LIMIT_REACHED)
        get_stream_writer()({"kind": "document_created", "document": ref.as_event()})
        text = f"I've made **{_plain(ref.title)}** into a PDF. It's being prepared now."
        return {"messages": [AIMessage(content=text)]}

    return document_persist
