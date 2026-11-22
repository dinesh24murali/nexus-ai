"""Chat runs: the run lease, streaming, cancel, regenerate and graceful drain (ADR-0006).

A run is a detached asyncio task. It drives the LangGraph graph, pushes typed events into a queue
that the HTTP response drains, and persists the result when it ends. Because the task does not
depend on the response, a client that disconnects does not stop it; only `cancel()` does.

Lifecycle of one run
  1. one atomic UPDATE takes the conversation's lease (`active_run_id`); a second concurrent send
     matches nothing and gets 409 `RUN_IN_PROGRESS`
  2. the user message is stored in the same transaction
  3. the graph streams tokens; a heartbeat refreshes `run_heartbeat_at` every 10 s so the worker's
     reaper can tell a live run from a dead one
  4. ONE transaction ends the run: release the lease, write the assistant message, add the spend to
     `usage_daily`, enqueue the title job. If the lease is no longer ours (the reaper took it), the
     run writes nothing, so a message can never be duplicated

How a run can end: `complete`, `cancelled` (the partial text is kept and appended to the thread so
the model sees what it said), `failed` (model error, or still running at the drain deadline).
"""

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

import openai
import structlog
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.sse import (
    Citation,
    CitationEvent,
    DocumentCreatedEvent,
    DocumentObject,
    DoneEvent,
    ErrorEvent,
    Event,
    InterruptEvent,
    MemoryUpdatedEvent,
    PendingInterrupt,
    ProgressEvent,
    RouteSelectedEvent,
    RunStartedEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
    Usage,
    UsageEvent,
)
from app.core import ratelimit
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.runtime_flags import RuntimeFlags
from app.db.repositories import conversations, messages, users
from app.db.repositories import usage as usage_repo
from app.graph.builder import build_graph
from app.graph.commands import ParsedCommand, StaticReply, parse_command, unavailable_reply
from app.graph.intents import PIPELINES
from app.graph.intents.deep_research import PlanError, ResearchChecker, clean_queries
from app.graph.intents.generate_document import DocumentService
from app.graph.intents.manage_memory import MemoryManager
from app.graph.nodes.load_context import MemoryLookup, RecentFiles
from app.graph.state import MAX_TOOL_CALLS
from app.graph.tools.web_search import MAX_QUERY_CHARS
from app.jobs import queue
from app.jobs.handlers import JOBS
from app.llm.factory import LLMConfigError, build_web_searcher, get_model
from app.llm.moderation import Moderator, moderation_flagged
from app.llm.prompts import load_prompt
from app.llm.redaction import hash_user_id
from app.llm.tracing import RunTrace, Tracer
from app.llm.usage import UsageCollector
from app.rag.retriever import Retriever
from app.services import usage as usage_service

_log = structlog.get_logger("nexus.runs")

Factory = async_sessionmaker[AsyncSession]
Graph = CompiledStateGraph[Any, Any, Any, Any]
GraphFactory = Callable[[str | None], Graph]

HEARTBEAT_INTERVAL_S = 10.0  # ADR-0006
DRAIN_TIMEOUT_S = 90.0  # ADR-0006
_FORCE_STOP_WAIT_S = 10.0
_RETRY_AFTER_S = "10"
_TRACED_PROMPTS = (
    "nexus.system",
    "nexus.context",
    "nexus.summarize",
    "nexus.route",
    "nexus.document",
    "nexus.research_plan",
    "nexus.research_report",
)
# The nodes whose model tokens are the answer: the chat agent, and the research report (doc 08
# section 5). The planner, the router, the summariser and the document composer stay silent.
_ANSWER_NODES = frozenset({"agent", "research_report"})
_RATE_WINDOW_S = 60
_RATE_LIMITED_MESSAGE = "You're sending messages too quickly. Please wait a moment."

StopReason = Literal["cancel", "drain", "lost"]
FinishReason = Literal["stop", "length", "cancelled", "tool_limit"]
RunKind = Literal["send", "regenerate", "resume"]
TraceName = Literal["chat.turn", "chat.regenerate", "chat.resume"]
_DANGLING_CALL_TEXT = (
    "This tool call did not run: the conversation moved on before it was completed."
)


@dataclass(frozen=True)
class RoutingFlags:
    """The kill switches that shape routing, read once when a run starts (a 60 s cache)."""

    router_disabled: bool = False  # ROUTER_DISABLED: every message is a chat message
    disabled_intents: frozenset[str] = frozenset()  # INTENTS_DISABLED
    memory_writes_disabled: bool = False  # MEMORY_WRITES_DISABLED: no save or forget by chat


@dataclass(eq=False)
class Run:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    user_message_id: uuid.UUID | None
    assistant_message_id: uuid.UUID
    replace_message_id: uuid.UUID | None
    override: str | None  # CHAT_MODEL_OVERRIDE at the time the run started
    default_model: str
    kind: RunKind = "send"  # a regenerate or a resume is not "a message sent" in usage_daily
    disabled: frozenset[str] = frozenset()  # TOOLS_DISABLED at the time the run started
    routing: RoutingFlags = field(default_factory=lambda: RoutingFlags())
    command: ParsedCommand | None = None  # a slash command that selects a pipeline
    static_reply: StaticReply | None = None  # /help or a usage reply: no graph, no model
    intent: str | None = None  # the pipeline that answered (from the `route` event or the state)
    route_source: str | None = None
    events: asyncio.Queue[Event | None] = field(default_factory=asyncio.Queue)  # None ends it
    done: asyncio.Event = field(default_factory=asyncio.Event)
    partial: list[str] = field(default_factory=list)  # answer text streamed so far
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # finished calls, API shape
    citations: list[dict[str, Any]] = field(default_factory=list)
    _calls_in_flight: dict[str, ToolStartEvent] = field(default_factory=dict)
    trace_tags: tuple[str, ...] = ()  # e.g. `moderation:skipped`
    persisted: bool = False  # the outcome was written (so spend changed)
    stop_reason: StopReason | None = None
    inner: asyncio.Task[Any] | None = None
    collector: UsageCollector = field(init=False)

    def __post_init__(self) -> None:
        self.collector = UsageCollector(default_model=self.default_model)

    @property
    def text(self) -> str:
        return "".join(self.partial)


@dataclass(frozen=True)
class _Result:
    text: str
    finish_reason: FinishReason


@dataclass(frozen=True)
class _Paused:
    """The graph stopped at an approval: the run ends here and the user decides later."""

    interrupt: PendingInterrupt


def graph_factory_for(
    settings: Settings,
    checkpointer: BaseCheckpointSaver[Any],
    *,
    retriever: Callable[[], Retriever] | None = None,
    recent_files: RecentFiles | None = None,
    memories: MemoryLookup | None = None,
    memory_manager: MemoryManager | None = None,
    document_service: DocumentService | None = None,
    research_service: ResearchChecker | None = None,
) -> GraphFactory:
    """The production graph: factory-built models, the Postgres checkpointer, the file-search tool
    (when a retriever is given), `web_search` (when Tavily is configured, or in fake mode) and the
    calculator. The retriever is built on first use, so a missing API key fails the run (like the
    chat model) and not the start of the API. Which tools a run may use is decided per run
    (`TOOLS_DISABLED`), not here. `memories` reads the user's long-term memories into the late
    context message. The router model is always built (ADR-0010); `ROUTER_DISABLED` is a per-run
    switch. `memory_manager` is what `manage_memory` acts through; `document_service` is what
    `generate_document` reads and writes (the pipeline is absent without it); `research_service`
    is the pre-check of `deep_research` (the pipeline is absent without it)."""

    def build(model_override: str | None) -> Graph:
        chat = get_model("chat", settings=settings, model_override=model_override)
        return build_graph(
            checkpointer=checkpointer,
            chat_model=chat,
            retriever=retriever() if retriever else None,
            web_searcher=build_web_searcher(settings),
            calculator=True,
            recent_files=recent_files,
            memories=memories,
            router_model=get_model("router", settings=settings),
            memory_manager=memory_manager,
            document_service=document_service,
            document_model=get_model("long_form", settings=settings) if document_service else None,
            research_service=research_service,
            research_plan_model=(
                get_model("research_plan", settings=settings) if research_service else None
            ),
            research_model=get_model("long_form", settings=settings) if research_service else None,
            settings=settings,
        )

    return build


def _already_running() -> ApiError:
    return ApiError(409, "RUN_IN_PROGRESS", "This conversation already has a run in progress.")


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Conversation not found.")


def _no_pending() -> ApiError:
    return ApiError(
        409,
        "NO_PENDING_INTERRUPT",
        "There is no pending approval to answer (it may have expired or been answered).",
    )


def _bad_edit(reason: str, field: str = "query") -> ApiError:
    loc = ["body", "decision"] if field == "decision" else ["body", "edited_args", field]
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "The request body is invalid.",
        details={"fields": [{"loc": loc, "msg": reason, "type": "value_error"}]},
    )


def _approval_expired(pending: dict[str, Any], now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(str(pending["expires_at"]))
    except KeyError, ValueError:
        return True  # an approval we cannot date is not one we act on
    return expires.tzinfo is None or expires <= now


def _resume_value(
    pending: dict[str, Any], decision: str, edited_args: dict[str, Any] | None
) -> Any:
    """The value `interrupt()` returns inside the graph, validated against the approval."""
    if pending.get("tool") == "forget_memory":
        return _forget_resume_value(pending, decision, edited_args)
    if pending.get("tool") == "research_plan":
        return _research_resume_value(decision, edited_args)
    if decision != "edit":
        return {"decision": decision}
    query = (edited_args or {}).get("query")
    if pending.get("tool") != "web_search" or not isinstance(query, str):
        raise _bad_edit("edited_args.query is required to edit a web search")
    query = query.strip()
    if not 1 <= len(query) <= MAX_QUERY_CHARS:
        raise _bad_edit(f"edited_args.query must be 1 to {MAX_QUERY_CHARS} characters")
    return {"decision": "edit", "args": {"query": query}}


def _research_resume_value(decision: str, edited_args: dict[str, Any] | None) -> Any:
    """A `research_plan` decision: approve the plan as shown, reject it, or edit the queries (1 to
    6, each 1 to 400 characters). A bad list is a 422 and the approval stays pending, so a typo
    cannot lose the card; nothing but these queries can ever be searched."""
    if decision != "edit":
        return {"decision": decision}
    try:
        queries = clean_queries((edited_args or {}).get("queries"), minimum=1)
    except PlanError:
        raise _bad_edit(
            "edited_args.queries must be 1 to 6 different queries of 1 to 400 characters",
            "queries",
        ) from None
    return {"decision": "edit", "args": {"queries": queries}}


def _forget_resume_value(
    pending: dict[str, Any], decision: str, edited_args: dict[str, Any] | None
) -> Any:
    """A `forget_memory` decision: `reject`, or `approve` naming one of the memories that were
    offered (the id may be left out when only one was). Anything else is a 422 and the approval
    stays pending, so a typo cannot delete or lose the card."""
    if decision == "reject":
        return {"decision": "reject"}
    if decision == "edit":
        raise _bad_edit("a memory can be approved or rejected, not edited", "decision")
    offered = [str(c.get("memory_id")) for c in (pending.get("args") or {}).get("candidates") or []]
    wanted = (edited_args or {}).get("memory_id")
    if wanted is None:
        if len(offered) != 1:
            raise _bad_edit(
                "edited_args.memory_id is required to pick one of the memories", "memory_id"
            )
        wanted = offered[0]
    if str(wanted) not in offered:
        raise _bad_edit("edited_args.memory_id is not one of the offered memories", "memory_id")
    return {"decision": "approve", "args": {"memory_id": str(wanted)}}


def _no_regenerate() -> ApiError:
    return ApiError(
        409,
        "REGENERATE_NOT_SUPPORTED",
        "Only the latest assistant answer can be regenerated.",
    )


def _error_event(exc: BaseException) -> ErrorEvent:
    """A model failure as a user-safe event. The exception text never reaches the client."""
    if (
        isinstance(exc, openai.BadRequestError)
        and getattr(exc, "code", None) == "context_length_exceeded"
    ):
        return ErrorEvent(
            code="CONTEXT_TOO_LONG",
            message="This conversation is too long for the model. Start a new chat.",
            retryable=False,
        )
    if isinstance(
        exc,
        openai.APITimeoutError
        | openai.APIConnectionError
        | openai.InternalServerError
        | openai.RateLimitError,
    ):
        return ErrorEvent(
            code="LLM_UNAVAILABLE",
            message="The model is unavailable right now. Try again.",
            retryable=True,
        )
    return ErrorEvent(code="INTERNAL", message="Something went wrong.", retryable=False)


def _finish_reason(message: AIMessage) -> FinishReason:
    meta = message.response_metadata or {}
    incomplete = (meta.get("incomplete_details") or {}).get("reason")
    if incomplete == "max_output_tokens" or meta.get("finish_reason") == "length":
        return "length"
    return "stop"


class RunService:
    def __init__(
        self,
        *,
        factory: Factory,
        settings: Settings,
        graph_factory: GraphFactory,
        flags: RuntimeFlags | Any | None = None,
        tracer: Tracer | None = None,
        moderator: Moderator | None = None,
        heartbeat_s: float = HEARTBEAT_INTERVAL_S,
        pipelines: frozenset[str] = PIPELINES,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._graph_factory = graph_factory
        self._flags = flags
        self._tracer = tracer
        self._moderator = moderator or Moderator(settings)
        self._heartbeat_s = heartbeat_s
        # The pipelines the graph this service drives can run: a slash command for any other is
        # explained ("not available yet") instead of silently answered by chat.
        self._pipelines = pipelines
        self._graphs: dict[str, Graph] = {}
        self._runs: dict[uuid.UUID, Run] = {}  # conversation id -> its active run (this process)
        self._draining = False

    # ---------------------------------------------------------------- starting runs

    async def start_send(
        self,
        user_id: uuid.UUID,
        display_name: str,
        conversation_id: uuid.UUID,
        content: str,
    ) -> Run:
        self._accepting()
        await self._admit(user_id)
        verdict = await self._moderator.check(
            content
        )  # before the lease: a slow API must not hold it
        if verdict.flagged:
            raise moderation_flagged()
        tags = ("moderation:skipped",) if verdict.skipped else ()
        override = await self._override()
        disabled = await self._disabled_tools()
        routing = await self._routing_flags()
        command, static = self._command_for(content, routing)
        if command is not None:
            tags = (*tags, "route:command", f"intent:{command.intent}")
        elif routing.router_disabled and static is None:
            tags = (*tags, "route:disabled")
        run_id, user_message_id = uuid.uuid4(), uuid.uuid4()
        async with self._factory() as session, session.begin():
            await self._acquire(session, user_id, conversation_id, run_id)
            # A new message abandons an approval nobody answered (functional spec FS-005).
            await conversations.clear_pending_interrupt(session, conversation_id)
            await messages.add(
                session,
                id=user_message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                role="user",
                content=content,
            )
        run = self._register(
            run_id, conversation_id, user_id, user_message_id, None, override, tags, disabled
        )
        run.routing, run.command, run.static_reply = routing, command, static
        if static is None:
            # The lease is ours: settle a tool call the last run left unanswered before the model
            # sees the thread again (an abandoned approval, a crash or a stop mid-tools). A static
            # reply never reaches the model or the thread.
            await self._repair_thread(run)
        message = HumanMessage(content=content, id=str(user_message_id))
        return self._launch(run, {"messages": [message]}, display_name, override, None, "chat.turn")

    async def start_resume(
        self,
        user_id: uuid.UUID,
        display_name: str,
        conversation_id: uuid.UUID,
        interrupt_id: str,
        decision: str,
        edited_args: dict[str, Any] | None = None,
    ) -> Run:
        """Answer the conversation's pending approval and stream the rest of the run.

        One transaction checks the approval (it is this one, it has not expired, the edit is
        valid), takes the lease and clears the approval, so a double click resumes only once. The
        approval is consumed before the graph runs: if the run then fails, the user asks again."""
        self._accepting()
        await self._admit(user_id)
        override = await self._override()
        disabled = await self._disabled_tools()
        routing = await self._routing_flags()
        run_id = uuid.uuid4()
        async with self._factory() as session, session.begin():
            conversation = await conversations.get(session, user_id, conversation_id)
            if conversation is None:
                raise _not_found()
            if conversation.active_run_id is not None:
                raise _already_running()
            pending = conversation.pending_interrupt
            now = datetime.now(UTC)
            if (
                not pending
                or pending.get("interrupt_id") != interrupt_id
                or _approval_expired(pending, now)
            ):
                raise _no_pending()
            value = _resume_value(pending, decision, edited_args)
            if not await conversations.take_pending_interrupt(
                session, user_id, conversation_id, run_id, interrupt_id
            ):
                raise _already_running()  # lost a race with another decision or a new message
        run = self._register(
            run_id, conversation_id, user_id, None, None, override, (), disabled, kind="resume"
        )
        run.routing = routing
        return self._launch(run, Command(resume=value), display_name, override, None, "chat.resume")

    async def start_regenerate(
        self, user_id: uuid.UUID, display_name: str, conversation_id: uuid.UUID
    ) -> Run:
        self._accepting()
        await self._admit(user_id)
        override = await self._override()
        disabled = await self._disabled_tools()
        run_id = uuid.uuid4()
        async with self._factory() as session, session.begin():
            conversation = await conversations.get(session, user_id, conversation_id)
            if conversation is None:
                raise _not_found()
            if conversation.active_run_id is not None:
                raise _already_running()
            last = await messages.latest(session, user_id, conversation_id)
            if last is None or last.role != "assistant" or not _regenerable(last):
                raise _no_regenerate()
            await self._acquire(session, user_id, conversation_id, run_id)
            replace_id = last.id

        # The lease is ours from here on: nothing else can add to the thread while we look at it.
        try:
            snapshot = await self._regenerate_point(override, conversation_id)
        except BaseException:
            await self._release_quietly(conversation_id, run_id)
            raise
        if snapshot is None:
            await self._release_quietly(conversation_id, run_id)
            raise _no_regenerate()

        run = self._register(
            run_id, conversation_id, user_id, None, replace_id, override, (), disabled
        )
        checkpoint_id = str(snapshot.config["configurable"]["checkpoint_id"])
        return self._launch(run, None, display_name, override, checkpoint_id, "chat.regenerate")

    async def cancel(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
        """Ask the active run of this conversation to stop. False if none runs in this process."""
        run = self._runs.get(conversation_id)
        if run is None or run.user_id != user_id:
            return False
        self._stop(run, "cancel")
        return True

    async def cancel_and_wait(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        """Stop the run and wait until it has been saved (used before deleting a conversation)."""
        run = self._runs.get(conversation_id)
        if run is None or run.user_id != user_id:
            return
        self._stop(run, "cancel")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run.done.wait(), timeout=_FORCE_STOP_WAIT_S)

    # ---------------------------------------------------------------- draining

    @property
    def draining(self) -> bool:
        return self._draining

    def begin_drain(self) -> None:
        """Refuse new runs (503 `SERVICE_RESTARTING`); running ones continue."""
        self._draining = True

    async def drain(self, *, timeout_s: float = DRAIN_TIMEOUT_S) -> None:
        """Wait for running runs; at the deadline save whatever is left as `failed`."""
        self.begin_drain()
        active = list(self._runs.values())
        if not active:
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout_s):
                await asyncio.gather(*(r.done.wait() for r in active))
        leftovers = [r for r in active if not r.done.is_set()]
        for run in leftovers:
            self._stop(run, "drain")
        if leftovers:
            _log.warning("runs_forced_at_drain_deadline", count=len(leftovers))
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(_FORCE_STOP_WAIT_S):
                    await asyncio.gather(*(r.done.wait() for r in leftovers))

    # ---------------------------------------------------------------- internals: start

    def _accepting(self) -> None:
        if self._draining:
            raise ApiError(
                503,
                "SERVICE_RESTARTING",
                "The server is restarting. Please try again in a moment.",
                headers={"Retry-After": _RETRY_AFTER_S},
            )

    async def _admit(self, user_id: uuid.UUID) -> None:
        """Per-user rate limit, then the daily budgets: both refuse before anything is written."""
        now = datetime.now(UTC)
        await ratelimit.hit(
            self._factory,
            f"chat:{user_id}",
            self._settings.chat_rate_limit_per_min,
            now=now,
            window_s=_RATE_WINDOW_S,
            message=_RATE_LIMITED_MESSAGE,
        )
        await usage_service.ensure_within_budgets(self._factory, self._settings, user_id, now=now)

    async def _note_spend(self) -> None:
        try:
            await usage_service.note_global_spend(
                self._factory, self._settings, now=datetime.now(UTC)
            )
        except Exception:
            _log.warning("spend_alert_check_failed", exc_info=True)

    async def _override(self) -> str | None:
        if self._flags is None:
            return None
        return (await self._flags.get("CHAT_MODEL_OVERRIDE")) or None

    async def _disabled_tools(self) -> frozenset[str]:
        """`TOOLS_DISABLED` (kill switch, read per run): tools that are neither offered nor run."""
        if self._flags is None:
            return frozenset()
        raw = (await self._flags.get("TOOLS_DISABLED")) or ""
        return frozenset(name.strip() for name in str(raw).split(",") if name.strip())

    async def _routing_flags(self) -> RoutingFlags:
        """`ROUTER_DISABLED`, `INTENTS_DISABLED` and `MEMORY_WRITES_DISABLED`, read per run."""
        if self._flags is None:
            return RoutingFlags()
        intents = str(await self._flags.get("INTENTS_DISABLED") or "")
        return RoutingFlags(
            router_disabled=str(await self._flags.get("ROUTER_DISABLED") or "") == "1",
            disabled_intents=frozenset(p.strip() for p in intents.split(",") if p.strip()),
            memory_writes_disabled=str(await self._flags.get("MEMORY_WRITES_DISABLED") or "")
            == "1",
        )

    def _command_for(
        self, content: str, routing: RoutingFlags
    ) -> tuple[ParsedCommand | None, StaticReply | None]:
        """The slash command at the start of a message, as (a pipeline to run, an answer that
        needs none). A command whose pipeline is not built or is switched off gets an explanation,
        never a silent fallback to chat."""
        parsed = parse_command(content, available=self._pipelines - routing.disabled_intents)
        if isinstance(parsed, StaticReply):
            return None, parsed
        if parsed is None:
            return None, None
        if parsed.intent not in self._pipelines:
            return None, unavailable_reply(parsed, disabled=False)
        if parsed.intent in routing.disabled_intents:
            return None, unavailable_reply(parsed, disabled=True)
        return parsed, None

    async def _acquire(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        if await conversations.get(session, user_id, conversation_id) is None:
            raise _not_found()
        if not await conversations.acquire_lease(session, user_id, conversation_id, run_id):
            raise _already_running()

    async def _release_quietly(self, conversation_id: uuid.UUID, run_id: uuid.UUID) -> None:
        try:
            async with self._factory() as session, session.begin():
                await conversations.release_lease(session, conversation_id, run_id)
        except Exception:
            _log.exception("lease_release_failed", conversation_id=str(conversation_id))

    def _graph(self, override: str | None) -> Graph:
        key = override or ""
        if key not in self._graphs:  # built lazily: a missing API key must not stop the API booting
            self._graphs[key] = self._graph_factory(override)
        return self._graphs[key]

    async def _regenerate_point(
        self, override: str | None, conversation_id: uuid.UUID
    ) -> StateSnapshot | None:
        """The checkpoint just before the last `agent` call (newest first): forking from it
        replays the final answer on top of the same history and summary."""
        try:
            graph = self._graph(override)
        except LLMConfigError:
            raise ApiError(503, "LLM_UNAVAILABLE", "The model is not configured.") from None
        config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
        async for snapshot in graph.aget_state_history(config):
            if tuple(snapshot.next) == ("agent",):
                return snapshot
        return None

    def _register(
        self,
        run_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        user_message_id: uuid.UUID | None,
        replace_message_id: uuid.UUID | None,
        override: str | None,
        trace_tags: tuple[str, ...] = (),
        disabled: frozenset[str] = frozenset(),
        *,
        kind: RunKind | None = None,
    ) -> Run:
        run = Run(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            user_message_id=user_message_id,
            assistant_message_id=messages.assistant_message_id(run_id),
            replace_message_id=replace_message_id,
            override=override,
            default_model=override or self._settings.llm_chat_model,
            kind=kind or ("regenerate" if replace_message_id is not None else "send"),
            disabled=disabled,
            trace_tags=trace_tags,
        )
        self._runs[conversation_id] = run
        return run

    def _launch(
        self,
        run: Run,
        graph_input: dict[str, Any] | Command[Any] | None,
        display_name: str,
        override: str | None,
        checkpoint_id: str | None,
        trace_name: TraceName,
    ) -> Run:
        asyncio.create_task(  # noqa: RUF006 - tracked in self._runs until it ends
            self._execute(run, graph_input, display_name, override, checkpoint_id, trace_name)
        )
        return run

    @staticmethod
    def _stop(run: Run, reason: StopReason) -> None:
        run.stop_reason = run.stop_reason or reason
        if run.inner is not None and not run.inner.done():
            run.inner.cancel()

    # ---------------------------------------------------------------- internals: the run

    async def _execute(
        self,
        run: Run,
        graph_input: dict[str, Any] | Command[Any] | None,
        display_name: str,
        override: str | None,
        checkpoint_id: str | None,
        trace_name: TraceName,
    ) -> None:
        beat: asyncio.Task[None] | None = None
        try:
            run.events.put_nowait(
                RunStartedEvent(
                    run_id=run.run_id,
                    user_message_id=run.user_message_id,
                    assistant_message_id=run.assistant_message_id,
                )
            )
            beat = asyncio.create_task(self._beat(run))
            run.inner = asyncio.create_task(
                self._drive(run, graph_input, display_name, override, checkpoint_id, trace_name)
            )
            if run.stop_reason is not None:  # cancel() arrived before the task existed
                run.inner.cancel()
            await self._settle(run)
        except Exception:
            _log.exception("run_crashed", run_id=str(run.run_id))
            run.events.put_nowait(
                ErrorEvent(code="INTERNAL", message="Something went wrong.", retryable=False)
            )
        finally:
            if beat is not None:
                beat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beat
            if run.persisted:  # the `done` event is already queued; only the close waits for this
                await self._note_spend()
            self._runs.pop(run.conversation_id, None)
            run.events.put_nowait(None)
            run.done.set()

    async def _settle(self, run: Run) -> None:
        """Wait for the graph task and persist however it ended."""
        assert run.inner is not None  # noqa: S101 - set just before
        try:
            result = await run.inner
        except asyncio.CancelledError:
            if run.stop_reason is None:
                raise  # the event loop is shutting down, not a cancel/drain request
            await self._after_stop(run)
        except Exception as exc:
            _log.exception(
                "run_failed", run_id=str(run.run_id), conversation_id=str(run.conversation_id)
            )
            await self._finish(
                run,
                status="failed",
                finish_reason="error",
                text=run.text,
                error=_error_event(exc),
            )
        else:
            if isinstance(result, _Paused):
                await self._park(run, result)
            else:
                await self._finish(
                    run, status="complete", finish_reason=result.finish_reason, text=result.text
                )

    async def _after_stop(self, run: Run) -> None:
        if run.stop_reason == "cancel":
            await self._repair_thread(run)  # stopped between the model and its tools?
            await self._finish(
                run,
                status="cancelled",
                finish_reason="cancelled",
                text=run.text,
                append_to_thread=bool(run.text),
            )
        elif run.stop_reason == "drain":
            await self._finish(
                run,
                status="failed",
                finish_reason="server_restart",
                text=run.text,
                error=ErrorEvent(
                    code="INTERNAL",
                    message="The server is restarting. Please try again.",
                    retryable=True,
                ),
            )
        else:  # lost: the reaper owns the conversation now, we write nothing
            run.events.put_nowait(
                ErrorEvent(code="INTERNAL", message="The run was interrupted.", retryable=True)
            )

    async def _beat(self, run: Run) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            try:
                async with self._factory() as session, session.begin():
                    holding = await conversations.heartbeat_lease(
                        session, run.conversation_id, run.run_id
                    )
            except Exception:
                _log.warning("run_heartbeat_failed", run_id=str(run.run_id), exc_info=True)
                continue
            if not holding:
                _log.warning("run_lease_lost", run_id=str(run.run_id))
                self._stop(run, "lost")
                return

    def _config(
        self,
        run: Run,
        display_name: str,
        checkpoint_id: str | None,
        trace_name: TraceName,
    ) -> RunnableConfig:
        configurable: dict[str, Any] = {
            "thread_id": str(run.conversation_id),
            "user_id": str(run.user_id),  # trusted per-run value; never taken from model output
            "user_display_name": display_name,
            "assistant_message_id": str(run.assistant_message_id),  # trusted: ties a document to it
            "today": datetime.now(UTC).date().isoformat(),
            "disabled_tools": sorted(run.disabled),  # TOOLS_DISABLED, a trusted per-run value
            "router_disabled": run.routing.router_disabled,
            "disabled_intents": sorted(run.routing.disabled_intents),
            "memory_writes_disabled": run.routing.memory_writes_disabled,
        }
        if run.command is not None:  # parsed by code before the graph: skips the router
            configurable["command"] = {
                "name": run.command.name,
                "intent": run.command.intent,
                "slots": run.command.slots,
            }
        if checkpoint_id:
            configurable.update(checkpoint_id=checkpoint_id, checkpoint_ns="")
        config: dict[str, Any] = {"configurable": configurable, "callbacks": [run.collector]}
        if self._tracer is not None:
            trace = RunTrace(
                name=trace_name,
                conversation_id=str(run.conversation_id),
                user_hash=hash_user_id(
                    str(run.user_id), secret=self._settings.app_secret.get_secret_value()
                ),
                run_id=str(run.run_id),
                assistant_message_id=str(run.assistant_message_id),
                prompts=[load_prompt(name) for name in _TRACED_PROMPTS],
                tags=run.trace_tags,
            )
            traced = self._tracer.run_config(trace)
            config["callbacks"] += traced.pop("callbacks", [])
            config.update(traced)
        return cast("RunnableConfig", config)

    async def _drive(
        self,
        run: Run,
        graph_input: dict[str, Any] | Command[Any] | None,
        display_name: str,
        override: str | None,
        checkpoint_id: str | None,
        trace_name: TraceName,
    ) -> _Result | _Paused:
        if run.static_reply is not None:  # /help or a usage reply: no graph, no model, no thread
            return self._answer_statically(run, run.static_reply)
        graph = self._graph(override)
        config = self._config(run, display_name, checkpoint_id, trace_name)
        thread: RunnableConfig = {"configurable": {"thread_id": str(run.conversation_id)}}
        if run.kind != "send":
            # A resume and a regenerate continue a turn that was routed earlier: the pipeline it
            # chose is in the state (a resume never routes again).
            before = await graph.aget_state(thread)
            route = before.values.get("route") or {}
            run.intent = route.get("intent") or run.intent
            run.route_source = route.get("source") or run.route_source
        if run.kind == "resume":
            # The turn started in an earlier run: its sources and tool calls are in the state.
            run.citations = [
                Citation.model_validate(c).model_dump(mode="json")
                for c in before.values.get("citations") or []
            ]
            run.tool_calls = [dict(c) for c in before.values.get("tool_log") or []]
        stream = graph.astream(graph_input, config, stream_mode=["messages", "custom"])
        async for item in stream:
            mode, payload = cast("tuple[str, Any]", item)
            if mode == "custom":
                self._on_custom(run, payload)
                continue
            chunk, meta = payload
            # The summariser is tagged `nostream`; the node filter is the second line of defence.
            if not isinstance(meta, dict) or meta.get("langgraph_node") not in _ANSWER_NODES:
                continue
            if not isinstance(chunk, AIMessageChunk):
                continue
            if delta := chunk.text:
                run.partial.append(delta)
                run.events.put_nowait(TokenEvent(delta=delta))

        state = await graph.aget_state(thread)
        if state.interrupts:  # stopped at an approval: not an answer, the user decides first
            return _Paused(PendingInterrupt.model_validate(state.interrupts[0].value))
        final = _last_answer(state.values.get("messages", []))
        if final is None:
            raise RuntimeError("the graph finished without an answer")
        if not run.partial and final.text:  # a model that did not stream: send it in one piece
            run.partial.append(final.text)
            run.events.put_nowait(TokenEvent(delta=final.text))
        out_of_calls = state.values.get("tool_calls_this_turn", 0) >= MAX_TOOL_CALLS
        reason = _finish_reason(final)
        return _Result(
            text=final.text,
            finish_reason="tool_limit" if out_of_calls and reason == "stop" else reason,
        )

    @staticmethod
    def _answer_statically(run: Run, reply: StaticReply) -> _Result:
        """The whole answer at once. It has no pipeline, so it stores no intent; `route_source`
        `command` marks it (a regenerate of it is refused)."""
        run.route_source = "command"
        run.partial.append(reply.text)
        run.events.put_nowait(TokenEvent(delta=reply.text))
        return _Result(text=reply.text, finish_reason="stop")

    def _on_custom(self, run: Run, payload: Any) -> None:
        """Turn the tools node's custom events into SSE events and the stored read model."""
        if not isinstance(payload, dict):
            return
        match payload.get("kind"):
            case "route":
                routed = RouteSelectedEvent(
                    intent=payload["intent"],
                    source=payload["source"],
                    confidence=payload.get("confidence"),
                )
                run.intent, run.route_source = routed.intent, routed.source
                run.events.put_nowait(routed)
            case "progress":
                run.events.put_nowait(
                    ProgressEvent(
                        stage=payload["stage"], done=payload.get("done"), total=payload.get("total")
                    )
                )
            case "document_created":
                run.events.put_nowait(
                    DocumentCreatedEvent(
                        document=DocumentObject.model_validate(payload["document"])
                    )
                )
            case "memory_updated":
                run.events.put_nowait(
                    MemoryUpdatedEvent(action=payload["action"], content=payload["content"])
                )
            case "tool_start":
                event = ToolStartEvent(
                    call_id=payload["call_id"], name=payload["name"], args=payload["args"]
                )
                run._calls_in_flight[event.call_id] = event
                # Text streamed before a tool call is not the answer: keep only what follows it.
                run.partial.clear()
                run.events.put_nowait(event)
            case "citation":
                citation = Citation.model_validate(payload["citation"])
                run.citations.append(citation.model_dump(mode="json"))
                run.events.put_nowait(CitationEvent(citation=citation))
            case "tool_end":
                end = ToolEndEvent(
                    call_id=payload["call_id"],
                    name=payload["name"],
                    status=payload["status"],
                    summary=payload.get("summary") or "",
                )
                started = run._calls_in_flight.pop(end.call_id, None)
                run.tool_calls.append(
                    {
                        "id": end.call_id,
                        "name": end.name,
                        "args": started.args if started else {},
                        "status": end.status,
                        **({"summary": end.summary} if end.summary else {}),
                    }
                )
                run.events.put_nowait(end)
            case "spend":  # internal: the query embedding is paid for, but the client never sees it
                run.collector.add_embedding(
                    self._settings.llm_embedding_model, int(payload["embedding_tokens"])
                )

    # ---------------------------------------------------------------- internals: finishing

    async def _finish(
        self,
        run: Run,
        *,
        status: Literal["complete", "cancelled", "failed"],
        finish_reason: str,
        text: str,
        error: ErrorEvent | None = None,
        append_to_thread: bool = False,
    ) -> None:
        used = run.collector
        total = used.total
        persisted = False
        try:
            async with self._factory() as session, session.begin():
                if not await conversations.release_lease(session, run.conversation_id, run.run_id):
                    _log.warning("run_lease_lost_at_finish", run_id=str(run.run_id))
                else:
                    await self._write_outcome(session, run, status, finish_reason, text)
                    persisted = True
                    run.persisted = True
        except Exception:
            _log.exception("run_persist_failed", run_id=str(run.run_id))
            run.events.put_nowait(
                ErrorEvent(code="INTERNAL", message="Something went wrong.", retryable=False)
            )
            await self._release_quietly(run.conversation_id, run.run_id)
            return

        if not persisted:
            run.events.put_nowait(
                ErrorEvent(code="INTERNAL", message="The run was interrupted.", retryable=True)
            )
            return
        if append_to_thread:
            await self._append_to_thread(run, text)
        if error is not None:
            run.events.put_nowait(error)
            return
        run.events.put_nowait(
            UsageEvent(
                usage=Usage(
                    model=used.main_model,
                    prompt_tokens=total.prompt_tokens,
                    cached_tokens=total.cached_tokens,
                    completion_tokens=total.completion_tokens,
                    cost_usd=used.cost_usd,
                )
            )
        )
        reason: FinishReason = (
            "cancelled" if status == "cancelled" else cast(FinishReason, finish_reason)
        )
        run.events.put_nowait(DoneEvent(message_id=run.assistant_message_id, finish_reason=reason))

    async def _park(self, run: Run, paused: _Paused) -> None:
        """The run stops at an approval: ONE transaction releases the lease, keeps the approval on
        the conversation and records the spend so far. No assistant message exists yet; the run
        that resumes writes it. The stream ends with the `interrupt` event."""
        try:
            async with self._factory() as session, session.begin():
                parked = await conversations.park_run(
                    session,
                    run.conversation_id,
                    run.run_id,
                    paused.interrupt.model_dump(mode="json"),
                )
                if parked:
                    if run.replace_message_id is not None:
                        # A regenerate forked the thread, so the old answer is no longer part of
                        # it; the resumed run has no old message to replace.
                        await messages.delete_message(session, run.user_id, run.replace_message_id)
                    await self._add_spend(session, run)
                    run.persisted = True
        except Exception:
            _log.exception("run_park_failed", run_id=str(run.run_id))
            run.events.put_nowait(
                ErrorEvent(code="INTERNAL", message="Something went wrong.", retryable=False)
            )
            await self._release_quietly(run.conversation_id, run.run_id)
            return
        if not run.persisted:
            _log.warning("run_lease_lost_at_park", run_id=str(run.run_id))
            run.events.put_nowait(
                ErrorEvent(code="INTERNAL", message="The run was interrupted.", retryable=True)
            )
            return
        run.events.put_nowait(InterruptEvent(interrupt=paused.interrupt))

    async def _add_spend(self, session: AsyncSession, run: Run) -> None:
        total = run.collector.total
        await usage_repo.add(
            session,
            run.user_id,
            datetime.now(UTC).date(),
            prompt_tokens=total.prompt_tokens,
            completion_tokens=total.completion_tokens,
            cost_usd=run.collector.cost_usd,
            # A regenerate re-runs an existing message and a resume continues one: both cost money
            # but are not a message sent.
            requests=1 if run.kind == "send" else 0,
        )

    async def _write_outcome(
        self,
        session: AsyncSession,
        run: Run,
        status: str,
        finish_reason: str,
        text: str,
    ) -> None:
        used = run.collector
        total = used.total
        if run.replace_message_id is not None:
            # Regenerate: the forked thread no longer contains the old answer, so neither does the
            # read model (whatever the outcome of the new run).
            await messages.delete_message(session, run.user_id, run.replace_message_id)
        await messages.add(
            session,
            id=run.assistant_message_id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            role="assistant",
            content=text,
            status=status,
            finish_reason=finish_reason,
            model=used.main_model,
            prompt_tokens=total.prompt_tokens,
            cached_tokens=total.cached_tokens,
            completion_tokens=total.completion_tokens,
            cost_usd=used.cost_usd,
            # The trace this run was exported under (None without tracing): a later rating of
            # the answer is scored on it.
            trace_id=(
                self._tracer.trace_id_for(str(run.assistant_message_id)) if self._tracer else None
            ),
            tool_calls=run.tool_calls,
            citations=run.citations,
            intent=run.intent,
            route_source=run.route_source,
        )
        await self._add_spend(session, run)
        if status == "complete":
            # Idempotent per conversation: only the first completed turn creates the job.
            await queue.enqueue(
                session,
                "generate_title",
                {"conversation_id": str(run.conversation_id)},
                f"title:{run.conversation_id}",
                user_id=run.user_id,
                max_attempts=JOBS["generate_title"].max_attempts,
            )
            await self._enqueue_memory_extraction(session, run)

    async def _enqueue_memory_extraction(self, session: AsyncSession, run: Run) -> None:
        """Queue the memory job for a finished turn, in the run's own transaction.

        A send and a resume (the answer that ends a turn paused for approval) are turns; a
        regenerate answers a message that was already read. Only users with memory on get a job:
        the job checks again when it runs, as does `MEMORY_WRITES_DISABLED`."""
        if run.kind == "regenerate" or run.static_reply is not None:
            return
        if run.intent == "manage_memory":
            return  # the user managed memories directly: nothing to extract from this turn
        if run.intent in ("generate_document", "deep_research") and run.route_source == "command":
            return  # `/pdf`, `/brief ...`, `/research ...`: a command, not something the user
            # told Nexus about themselves
        user = await users.get_by_id(session, run.user_id)
        if user is None or not user.memory_enabled:
            return
        await queue.enqueue(
            session,
            "extract_memories",
            {"assistant_message_id": str(run.assistant_message_id)},
            f"mem:{run.assistant_message_id}",
            user_id=run.user_id,
            max_attempts=JOBS["extract_memories"].max_attempts,
        )

    async def _repair_thread(self, run: Run) -> None:
        """Answer a tool call the thread left open, so the next model call is valid.

        When the newest message is an AI message with tool calls, the run that made it never got to
        the tools (the user ignored an approval, the process died, a stop landed between the model
        and its tools) and the provider would refuse the thread. Every open call gets an error
        result. Best effort: a failure is logged, and the run itself then fails visibly."""
        try:
            graph = self._graph(run.override)
            config: RunnableConfig = {"configurable": {"thread_id": str(run.conversation_id)}}
            state = await graph.aget_state(config)
            history = state.values.get("messages") or []
            last = history[-1] if history else None
            if not (isinstance(last, AIMessage) and last.tool_calls):
                return
            results = [
                ToolMessage(
                    _DANGLING_CALL_TEXT,
                    tool_call_id=str(call["id"]),
                    name=call["name"],
                    status="error",
                )
                for call in last.tool_calls
            ]
            await graph.aupdate_state(config, {"messages": results}, as_node="agent")
        except Exception:
            _log.warning("thread_repair_failed", run_id=str(run.run_id), exc_info=True)

    async def _append_to_thread(self, run: Run, text: str) -> None:
        """Put the partial answer into the thread so the next turn's model sees what it said."""
        try:
            graph = self._graph(run.override)
            config: RunnableConfig = {"configurable": {"thread_id": str(run.conversation_id)}}
            await graph.aupdate_state(
                config, {"messages": [AIMessage(content=text)]}, as_node="agent"
            )
        except Exception:
            _log.exception("thread_append_failed", run_id=str(run.run_id))


def _regenerable(message: Any) -> bool:
    """Only a `general_chat` answer can be regenerated (doc 08 section 2.3). A message from before
    routing (no intent) was one. A `/help` or usage reply has no thread behind it."""
    if message.intent not in (None, "general_chat"):
        return False
    return not (message.intent is None and message.route_source == "command")


def _last_answer(history: list[BaseMessage]) -> AIMessage | None:
    for message in reversed(history):
        if isinstance(message, AIMessage):
            return message
    return None
