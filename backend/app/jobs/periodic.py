"""Periodic tasks run by the worker on fixed intervals (ADR-0006, ADR-0007).

All of them are idempotent and safe to run in several workers at once (row locks with SKIP LOCKED
or plain deletes), so a second worker never needs coordination. An exception is logged and the task
tries again at its next tick; it never takes the loop or the worker down.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.repositories import conversations, invites, messages, rate_limits
from app.db.repositories import documents as documents_repo
from app.documents import storage
from app.jobs import queue

_log = structlog.get_logger("nexus.periodic")

Factory = async_sessionmaker[AsyncSession]

RUN_STALE_AFTER = timedelta(seconds=60)  # ADR-0006: heartbeat every 10 s, reaped after 60 s
JOB_STALE_AFTER = timedelta(minutes=2)  # ADR-0007: job heartbeat every 30 s, requeued after 2 min
DEAD_INVITE_RETENTION = timedelta(days=30)
RATE_LIMIT_RETENTION = timedelta(days=1)  # the longest window is 15 minutes


@dataclass(frozen=True)
class Task:
    name: str
    interval_s: float
    fn: Callable[[], Awaitable[object]]


async def run_periodic(task: Task, stop: asyncio.Event) -> None:
    """Run `task` now and then every `interval_s` until `stop` is set."""
    while not stop.is_set():
        try:
            result = await task.fn()
            _log.debug("periodic_task_ran", task=task.name, result=result)
        except Exception:
            _log.exception("periodic_task_failed", task=task.name)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=task.interval_s)


async def reap_stale_runs(factory: Factory, *, stale_after: timedelta = RUN_STALE_AFTER) -> int:
    """Free every conversation whose run died (API crash, OOM, deploy past the drain deadline).

    The user sees a `failed` assistant message (`finish_reason=server_restart`) instead of a chat
    that stays locked. The text the dead process had streamed is gone, so the message is empty.
    """
    async with factory() as session, session.begin():
        stale = await conversations.stale_leases(session, stale_after)
        for conversation_id, user_id, run_id in stale:
            await messages.add(
                session,
                id=messages.assistant_message_id(run_id),
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
                content="",
                status="failed",
                finish_reason="server_restart",
            )
            await conversations.clear_lease(session, conversation_id)
    if stale:
        _log.warning("stale_runs_reaped", count=len(stale))
    return len(stale)


async def requeue_stale_jobs(factory: Factory) -> int:
    count = await queue.requeue_stale(factory, older_than=JOB_STALE_AFTER)
    if count:
        _log.warning("stale_jobs_requeued", count=count)
    return count


async def cleanup_invites(factory: Factory) -> int:
    async with factory() as session, session.begin():
        return await invites.delete_dead(session, datetime.now(UTC) - DEAD_INVITE_RETENTION)


async def cleanup_rate_limits(factory: Factory) -> int:
    async with factory() as session, session.begin():
        return await rate_limits.delete_older_than(
            session, datetime.now(UTC) - RATE_LIMIT_RETENTION
        )


async def cleanup_documents(factory: Factory, settings: Settings) -> int:
    """Keep each user's newest `documents_keep` documents (100): older rows go first, then their
    PDFs. A crash between the two leaves an orphan file under the user's own directory, which the
    account purge removes; it never leaves a row that points at a missing file."""
    async with factory() as session, session.begin():
        old = await documents_repo.beyond_newest(session, settings.documents_keep)
        removed = await documents_repo.delete_ids(session, [doc_id for doc_id, _, _ in old])
    for _, _, path in old:
        await asyncio.to_thread(storage.remove, settings.uploads_dir, path)
    if removed:
        _log.info("documents_cleaned", count=removed)
    return removed


def standard_tasks(settings: Settings, factory: Factory) -> list[Task]:
    return [
        Task("reap_stale_runs", 30, lambda: reap_stale_runs(factory)),
        Task("requeue_stale_jobs", 30, lambda: requeue_stale_jobs(factory)),
        Task("cleanup_invites", 86_400, lambda: cleanup_invites(factory)),
        Task("cleanup_rate_limits", 86_400, lambda: cleanup_rate_limits(factory)),
        Task("cleanup_documents", 86_400, lambda: cleanup_documents(factory, settings)),
    ]
