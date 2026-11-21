"""Job handlers and the registry the worker dispatches on (docs/02_outputs/08 section 9).

Each kind has a hard timeout (enforced by the worker, after which the attempt fails) and an attempt
limit. Handlers are idempotent: the queue can run a job twice after a crash.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from langchain_core.runnables import Runnable, RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.repositories import conversations, messages, usage
from app.jobs.queue import ClaimedJob
from app.llm.factory import Role, get_model
from app.llm.prompts import load_prompt
from app.llm.redaction import hash_user_id
from app.llm.tracing import RunTrace, Tracer
from app.llm.usage import UsageCollector

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


JOBS: dict[str, JobSpec] = {
    # "1 retry" (doc 08 section 9) = 2 attempts.
    "generate_title": JobSpec(generate_title, timeout_s=120, max_attempts=2),
}
