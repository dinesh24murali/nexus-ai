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
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.sse import (
    DoneEvent,
    ErrorEvent,
    Event,
    RunStartedEvent,
    TokenEvent,
    Usage,
    UsageEvent,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.runtime_flags import RuntimeFlags
from app.db.repositories import conversations, messages
from app.db.repositories import usage as usage_repo
from app.graph.builder import build_graph
from app.jobs import queue
from app.jobs.handlers import JOBS
from app.llm.factory import LLMConfigError, get_model
from app.llm.prompts import load_prompt
from app.llm.redaction import hash_user_id
from app.llm.tracing import RunTrace, Tracer
from app.llm.usage import UsageCollector

_log = structlog.get_logger("nexus.runs")

Factory = async_sessionmaker[AsyncSession]
Graph = CompiledStateGraph[Any, Any, Any, Any]
GraphFactory = Callable[[str | None], Graph]

HEARTBEAT_INTERVAL_S = 10.0  # ADR-0006
DRAIN_TIMEOUT_S = 90.0  # ADR-0006
_FORCE_STOP_WAIT_S = 10.0
_RETRY_AFTER_S = "10"
_TRACED_PROMPTS = ("nexus.system", "nexus.context", "nexus.summarize")

StopReason = Literal["cancel", "drain", "lost"]
FinishReason = Literal["stop", "length", "cancelled", "tool_limit"]


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
    events: asyncio.Queue[Event | None] = field(default_factory=asyncio.Queue)  # None ends it
    done: asyncio.Event = field(default_factory=asyncio.Event)
    partial: list[str] = field(default_factory=list)  # text streamed so far
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


def graph_factory_for(settings: Settings, checkpointer: BaseCheckpointSaver[Any]) -> GraphFactory:
    """The production graph: factory-built models, the Postgres checkpointer."""

    def build(model_override: str | None) -> Graph:
        chat = get_model("chat", settings=settings, model_override=model_override)
        return build_graph(checkpointer=checkpointer, chat_model=chat, settings=settings)

    return build


def _already_running() -> ApiError:
    return ApiError(409, "RUN_IN_PROGRESS", "This conversation already has a run in progress.")


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Conversation not found.")


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
        heartbeat_s: float = HEARTBEAT_INTERVAL_S,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._graph_factory = graph_factory
        self._flags = flags
        self._tracer = tracer
        self._heartbeat_s = heartbeat_s
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
        override = await self._override()
        run_id, user_message_id = uuid.uuid4(), uuid.uuid4()
        async with self._factory() as session, session.begin():
            await self._acquire(session, user_id, conversation_id, run_id)
            await messages.add(
                session,
                id=user_message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                role="user",
                content=content,
            )
        run = self._register(run_id, conversation_id, user_id, user_message_id, None, override)
        message = HumanMessage(content=content, id=str(user_message_id))
        return self._launch(run, {"messages": [message]}, display_name, override, None, "chat.turn")

    async def start_regenerate(
        self, user_id: uuid.UUID, display_name: str, conversation_id: uuid.UUID
    ) -> Run:
        self._accepting()
        override = await self._override()
        run_id = uuid.uuid4()
        async with self._factory() as session, session.begin():
            conversation = await conversations.get(session, user_id, conversation_id)
            if conversation is None:
                raise _not_found()
            if conversation.active_run_id is not None:
                raise _already_running()
            last = await messages.latest(session, user_id, conversation_id)
            if last is None or last.role != "assistant":
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

        run = self._register(run_id, conversation_id, user_id, None, replace_id, override)
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

    async def _override(self) -> str | None:
        if self._flags is None:
            return None
        return (await self._flags.get("CHAT_MODEL_OVERRIDE")) or None

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
        )
        self._runs[conversation_id] = run
        return run

    def _launch(
        self,
        run: Run,
        graph_input: dict[str, Any] | None,
        display_name: str,
        override: str | None,
        checkpoint_id: str | None,
        trace_name: Literal["chat.turn", "chat.regenerate"],
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
        graph_input: dict[str, Any] | None,
        display_name: str,
        override: str | None,
        checkpoint_id: str | None,
        trace_name: Literal["chat.turn", "chat.regenerate"],
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
            await self._finish(
                run, status="complete", finish_reason=result.finish_reason, text=result.text
            )

    async def _after_stop(self, run: Run) -> None:
        if run.stop_reason == "cancel":
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
        trace_name: Literal["chat.turn", "chat.regenerate"],
    ) -> RunnableConfig:
        configurable: dict[str, Any] = {
            "thread_id": str(run.conversation_id),
            "user_id": str(run.user_id),  # trusted per-run value; never taken from model output
            "user_display_name": display_name,
            "today": datetime.now(UTC).date().isoformat(),
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
            )
            traced = self._tracer.run_config(trace)
            config["callbacks"] += traced.pop("callbacks", [])
            config.update(traced)
        return cast("RunnableConfig", config)

    async def _drive(
        self,
        run: Run,
        graph_input: dict[str, Any] | None,
        display_name: str,
        override: str | None,
        checkpoint_id: str | None,
        trace_name: Literal["chat.turn", "chat.regenerate"],
    ) -> _Result:
        graph = self._graph(override)
        config = self._config(run, display_name, checkpoint_id, trace_name)
        async for chunk, meta in graph.astream(graph_input, config, stream_mode="messages"):
            # The summariser is tagged `nostream`; the node filter is the second line of defence.
            if not isinstance(meta, dict) or meta.get("langgraph_node") != "agent":
                continue
            if not isinstance(chunk, AIMessageChunk):
                continue
            if delta := chunk.text:
                run.partial.append(delta)
                run.events.put_nowait(TokenEvent(delta=delta))

        state = await graph.aget_state({"configurable": {"thread_id": str(run.conversation_id)}})
        final = _last_answer(state.values.get("messages", []))
        if final is None:
            raise RuntimeError("the graph finished without an answer")
        if not run.partial and final.text:  # a model that did not stream: send it in one piece
            run.partial.append(final.text)
            run.events.put_nowait(TokenEvent(delta=final.text))
        return _Result(text=final.text, finish_reason=_finish_reason(final))

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
        )
        await usage_repo.add(
            session,
            run.user_id,
            datetime.now(UTC).date(),
            prompt_tokens=total.prompt_tokens,
            completion_tokens=total.completion_tokens,
            cost_usd=used.cost_usd,
            requests=1,
        )
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


def _last_answer(history: list[BaseMessage]) -> AIMessage | None:
    for message in reversed(history):
        if isinstance(message, AIMessage):
            return message
    return None
