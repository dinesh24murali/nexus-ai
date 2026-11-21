"""Job handlers and the registry the worker dispatches on (docs/02_outputs/08 section 9).

Each kind has a hard timeout (enforced by the worker, after which the attempt fails) and an attempt
limit. Handlers are idempotent: the queue can run a job twice after a crash.
"""

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from langchain_core.runnables import Runnable, RunnableConfig
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_flags import RuntimeFlags
from app.db.repositories import conversations, files, messages, usage, users
from app.jobs.queue import ClaimedJob
from app.llm.factory import Role, build_embeddings_client, get_model
from app.llm.pricing import embedding_cost_usd
from app.llm.prompts import load_prompt
from app.llm.redaction import hash_user_id
from app.llm.tracing import RunTrace, Tracer
from app.llm.usage import UsageCollector
from app.memory.extract import (
    SIMILAR_MEMORIES_SHOWN,
    apply_plan,
    plan_ops,
)
from app.memory.store import open_memory_store
from app.rag.chunker import chunk_document
from app.rag.embedder import Embedder
from app.rag.index import QdrantIndex
from app.rag.parsers import ParseError, parse

_log = structlog.get_logger("nexus.jobs")

Factory = async_sessionmaker[AsyncSession]

MAX_TITLE_CHARS = 100
_PROMPT_INPUT_CHARS = 1_000  # a title needs the gist of the exchange, not all of it
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_QUOTES = "\"'`\u201c\u201d\u2018\u2019\u00ab\u00bb"
_TITLE_PREFIX = re.compile(r"^title\s*:\s*", re.IGNORECASE)


@dataclass
class JobContext:
    factory: Factory
    settings: Settings
    tracer: Tracer | None = None
    # Role -> model. Defaults to the factory (the only place model clients are built); tests inject.
    model_for: Callable[[Role], Runnable[Any, Any]] | None = None
    # Built on first use from settings (clients come from the factory); tests inject their own.
    embedder: Embedder | None = None
    index: QdrantIndex | None = None
    # Operator kill switches (read through a 60 s cache); built on first use, tests inject.
    flags: RuntimeFlags | None = None

    def get_flags(self) -> RuntimeFlags:
        if self.flags is None:
            self.flags = RuntimeFlags(self.factory, defaults=self.settings.flag_defaults())
        return self.flags

    def get_embedder(self) -> Embedder:
        if self.embedder is None:
            client = build_embeddings_client(self.settings)
            self.embedder = Embedder(client, self.settings.llm_embedding_model)
        return self.embedder

    def get_index(self) -> QdrantIndex:
        if self.index is None:
            client = AsyncQdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key.get_secret_value() or None,
            )
            self.index = QdrantIndex(client, alias=self.settings.qdrant_collection)
        return self.index

    def model(self, role: Role) -> Runnable[Any, Any]:
        if self.model_for is not None:
            return self.model_for(role)
        return get_model(role, settings=self.settings)


Handler = Callable[[JobContext, ClaimedJob], Awaitable[None]]


@dataclass(frozen=True)
class JobSpec:
    handler: Handler
    timeout_s: float
    max_attempts: int


def clean_title(raw: str) -> str | None:
    """Turn model output into a valid conversation title, or None if nothing usable is left."""
    text = " ".join(_CONTROL.sub(" ", raw).split())
    text = _TITLE_PREFIX.sub("", text.strip(_QUOTES + " ")).strip(_QUOTES + " ")
    text = text.rstrip(".。!?… ").strip(_QUOTES + " ")
    return text[:MAX_TITLE_CHARS].rstrip() or None


async def generate_title(ctx: JobContext, job: ClaimedJob) -> None:
    if job.user_id is None:
        raise ValueError("generate_title needs a user")
    user_id = job.user_id
    conversation_id = uuid.UUID(str(job.payload["conversation_id"]))

    async with ctx.factory() as session:
        pair = await messages.first_exchange(session, user_id, conversation_id)
    if pair is None:
        return  # nothing to title yet (or not this user's conversation)

    prompt = load_prompt("nexus.title")
    text = prompt.render(
        first_user_message=pair[0][:_PROMPT_INPUT_CHARS],
        first_answer=pair[1][:_PROMPT_INPUT_CHARS],
    )
    collector = UsageCollector(default_model=ctx.settings.llm_background_model)
    merged: dict[str, Any] = {"callbacks": [collector]}
    if ctx.tracer is not None:
        trace = RunTrace(
            name="job.generate_title",
            conversation_id=str(conversation_id),
            user_hash=hash_user_id(str(user_id), secret=ctx.settings.app_secret.get_secret_value()),
            run_id=f"job-{job.id}",
            prompts=[prompt],
        )
        traced = ctx.tracer.run_config(trace)
        merged.update({k: v for k, v in traced.items() if k != "callbacks"})
        merged["callbacks"] += traced.get("callbacks", [])
    reply = await ctx.model("title").ainvoke(text, cast("RunnableConfig", merged))

    title = clean_title(reply.text)
    async with ctx.factory() as session, session.begin():
        # Spend is recorded even when the output is unusable: the call was made and billed.
        used = collector.total
        if used.prompt_tokens or used.completion_tokens:
            await usage.add(
                session,
                user_id,
                datetime.now(UTC).date(),
                prompt_tokens=used.prompt_tokens,
                completion_tokens=used.completion_tokens,
                cost_usd=collector.cost_usd,
            )
        if title is not None:
            await conversations.set_generated_title(session, user_id, conversation_id, title)


MEMORY_QUERY_CHARS = 2_000  # how much of the user's message is embedded to find related memories


async def extract_memories(ctx: JobContext, job: ClaimedJob) -> None:
    """Read one finished turn and update the user's memories (doc 08 section 7).

    Only the user's own message is a source. Nothing happens when the user has turned memory off
    or the operator has set `MEMORY_WRITES_DISABLED`: both are checked here, not only when the job
    was queued, because either can change in between. Idempotent: a second run of the same turn
    finds its creates as duplicates and its updates and deletes already applied. Logs carry ids and
    counts, never memory text.
    """
    if job.user_id is None:
        raise ValueError("extract_memories needs a user")
    user_id = job.user_id
    assistant_message_id = uuid.UUID(str(job.payload["assistant_message_id"]))

    async with ctx.factory() as session:
        user = await users.get_by_id(session, user_id)
        turn = await messages.completed_turn(session, user_id, assistant_message_id)
    if user is None or user.status != "active" or not user.memory_enabled or turn is None:
        return
    if await ctx.get_flags().enabled("MEMORY_WRITES_DISABLED"):
        _log.info("memory_extract_skipped", reason="writes_disabled")
        return

    prompt = load_prompt("nexus.memory_extract")
    collector = UsageCollector(default_model=ctx.settings.llm_background_model)
    merged: dict[str, Any] = {"callbacks": [collector]}
    if ctx.tracer is not None:
        trace = RunTrace(
            name="job.extract_memories",
            conversation_id=str(turn.conversation_id),
            user_hash=hash_user_id(str(user_id), secret=ctx.settings.app_secret.get_secret_value()),
            run_id=f"job-{job.id}",
            prompts=[prompt],
            features=("memory",),
        )
        traced = ctx.tracer.run_config(trace)
        merged.update({k: v for k, v in traced.items() if k != "callbacks"})
        merged["callbacks"] += traced.get("callbacks", [])

    embedding_tokens = 0
    try:
        async with open_memory_store(ctx.settings.database_url, ctx.get_embedder()) as store:
            try:
                existing = await store.search(
                    user_id, turn.user_text[:MEMORY_QUERY_CHARS], limit=SIMILAR_MEMORIES_SHOWN
                )
                plan = await plan_ops(
                    ctx.model("memory_extract"),
                    turn.user_text,
                    turn.assistant_text,
                    existing,
                    config=cast("RunnableConfig", merged),
                    prompt=prompt,
                )
                applied = await apply_plan(store, user_id, turn.conversation_id, plan)
            finally:
                embedding_tokens = store.embedding_tokens
    finally:
        # The calls were made and billed whatever happened next (the retry is billed again).
        used = collector.total
        if used.prompt_tokens or used.completion_tokens or embedding_tokens:
            async with ctx.factory() as session, session.begin():
                await usage.add(
                    session,
                    user_id,
                    datetime.now(UTC).date(),
                    prompt_tokens=used.prompt_tokens + embedding_tokens,
                    completion_tokens=used.completion_tokens,
                    cost_usd=collector.cost_usd
                    + embedding_cost_usd(ctx.settings.llm_embedding_model, embedding_tokens),
                )
    _log.info(
        "memory_extract_done",
        created=applied.created,
        updated=applied.updated,
        deleted=applied.deleted,
        duplicates=applied.duplicates,
        missing=applied.missing,
        evicted=applied.evicted,
        rejected=plan.rejected,
    )


INGEST_FAILED_REASON = "Processing failed. Please upload the file again."
FILE_MISSING_REASON = "The stored file could not be found. Please upload it again."


async def ingest_file(ctx: JobContext, job: ClaimedJob) -> None:
    """Parse, chunk, embed and index one uploaded file (doc 08 section 6).

    Idempotent: point ids derive from (file, chunk index), so a second run overwrites the first.
    A file nobody can read (`ParseError`) fails for good with a reason the user sees; anything else
    is retried by the queue, and on the last attempt the file is marked failed so it never stays
    "processing". Logs carry ids and counts, never text.
    """
    if job.user_id is None:
        raise ValueError("ingest_file needs a user")
    user_id = job.user_id
    file_id = uuid.UUID(str(job.payload["file_id"]))

    async with ctx.factory() as session:
        row = await files.get(session, user_id, file_id)
    if row is None:
        return  # deleted (or never this user's): nothing to do
    async with ctx.factory() as session, session.begin():
        if not await files.mark_processing(session, user_id, file_id):
            return

    index = ctx.get_index()
    path = ctx.settings.uploads_dir / row.storage_path
    try:
        await index.ensure_collection()
        document = await asyncio.to_thread(parse, path, row.mime_type)
        chunks = await asyncio.to_thread(chunk_document, document)
        embedded = await ctx.get_embedder().embed([c.text for c in chunks])
        # The embeddings are paid for whatever happens next.
        async with ctx.factory() as session, session.begin():
            await usage.add(
                session,
                user_id,
                datetime.now(UTC).date(),
                prompt_tokens=embedded.tokens,
                completion_tokens=0,
                cost_usd=embedding_cost_usd(ctx.settings.llm_embedding_model, embedded.tokens),
            )
        await index.upsert(
            user_id=user_id,
            file_id=file_id,
            filename=row.filename,
            chunks=chunks,
            vectors=embedded.vectors,
        )
    except ParseError as exc:
        await _fail_file(ctx, user_id, file_id, exc.reason)
        _log.info("ingest_unreadable", file_id=str(file_id))
        return
    except FileNotFoundError:
        await _fail_file(ctx, user_id, file_id, FILE_MISSING_REASON)
        _log.warning("ingest_file_missing_on_disk", file_id=str(file_id))
        return
    except Exception:
        if job.attempts >= job.max_attempts:
            await _fail_file(ctx, user_id, file_id, INGEST_FAILED_REASON)
        raise

    async with ctx.factory() as session, session.begin():
        still_there = await files.mark_ready(
            session, user_id, file_id, chunk_count=len(chunks), page_count=document.page_count
        )
    if not still_there:
        await index.delete_file(user_id, file_id)  # deleted while we worked: leave nothing behind
        return
    _log.info("ingest_done", file_id=str(file_id), chunks=len(chunks), tokens=embedded.tokens)


async def _fail_file(ctx: JobContext, user_id: uuid.UUID, file_id: uuid.UUID, reason: str) -> None:
    """Mark the file failed and remove whatever a partial run indexed."""
    try:
        await ctx.get_index().delete_file(user_id, file_id)
    except Exception:
        _log.warning("ingest_cleanup_failed", file_id=str(file_id), exc_info=True)
    async with ctx.factory() as session, session.begin():
        await files.mark_failed(session, user_id, file_id, reason)


JOBS: dict[str, JobSpec] = {
    # "1 retry" (doc 08 section 9) = 2 attempts.
    "generate_title": JobSpec(generate_title, timeout_s=120, max_attempts=2),
    "ingest_file": JobSpec(ingest_file, timeout_s=300, max_attempts=3),
    # Doc 08 section 9 lists 2 for this job: 2 attempts, as `ingest_file` reads its 3 as 3 attempts.
    "extract_memories": JobSpec(extract_memories, timeout_s=120, max_attempts=2),
}
