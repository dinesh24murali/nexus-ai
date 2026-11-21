"""Postgres job queue (ADR-0007): `SELECT … FOR UPDATE SKIP LOCKED`, no broker.

Life of a job: `queued` -> `running` (claimed by a worker, `locked_at` refreshed by the worker's
heartbeat) -> `done`, or back to `queued` with backoff after a failure, or `failed` once its
attempts are spent. A `running` job whose heartbeat stopped (worker crash) is returned to the queue
by `requeue_stale`.

Every transition after the claim is conditional on `locked_by`, so a worker whose job was already
requeued and picked up by another worker can neither complete nor fail it.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job

Factory = async_sessionmaker[AsyncSession]

BACKOFF_BASE_S = 10
BACKOFF_MAX_S = 120
MAX_ERROR_CHARS = 500


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int  # including this one
    max_attempts: int
    user_id: uuid.UUID | None


def backoff_s(attempt: int) -> int:
    """Delay before retrying after the `attempt`-th failure: 10 s, 20 s, 40 s … capped at 2 min."""
    return min(BACKOFF_BASE_S << max(attempt - 1, 0), BACKOFF_MAX_S)


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    *,
    user_id: uuid.UUID | None = None,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> int | None:
    """Add a job in the caller's transaction. Returns None when the key already exists (the job
    was enqueued before), so a retried request or a replayed event never duplicates work."""
    values: dict[str, Any] = {
        "kind": kind,
        "payload": payload,
        "idempotency_key": idempotency_key,
        "user_id": user_id,
        "max_attempts": max_attempts,
    }
    if run_after is not None:
        values["run_after"] = run_after
    stmt = (
        insert(Job)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Job.idempotency_key])
        .returning(Job.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def claim(
    factory: Factory, worker_id: str, *, kinds: Sequence[str] | None = None
) -> ClaimedJob | None:
    """Take the oldest due job, or None. SKIP LOCKED lets concurrent workers pass over a row
    another worker is claiming instead of waiting for it."""
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= func.now())
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if kinds is not None:
        stmt = stmt.where(Job.kind.in_(list(kinds)))
    async with factory() as session, session.begin():
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_by = worker_id
        job.locked_at = func.now()
        await session.flush()
        return ClaimedJob(
            id=job.id,
            kind=job.kind,
            payload=dict(job.payload),
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            user_id=job.user_id,
        )


async def cancel_queued(
    session: AsyncSession, kind: str, user_id: uuid.UUID, payload_key: str, payload_value: str
) -> int:
    """Cancel the owner's jobs of `kind` that have not started (in the caller's transaction), e.g.
    the ingestion of a file that is being deleted. A job already `running` is left alone: it ends
    on its own, and its handler notices that what it worked on is gone."""
    stmt = (
        update(Job)
        .where(
            Job.kind == kind,
            Job.user_id == user_id,
            Job.status == "queued",
            Job.payload[payload_key].as_string() == payload_value,
        )
        .values(status="cancelled")
    )
    return int((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


def _held_by(job_id: int, worker_id: str) -> tuple[Any, ...]:
    return (Job.id == job_id, Job.status == "running", Job.locked_by == worker_id)


async def heartbeat(factory: Factory, job_id: int, worker_id: str) -> bool:
    stmt = update(Job).where(*_held_by(job_id, worker_id)).values(locked_at=func.now())
    async with factory() as session, session.begin():
        return bool((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


async def complete(factory: Factory, job_id: int, worker_id: str) -> bool:
    stmt = (
        update(Job)
        .where(*_held_by(job_id, worker_id))
        .values(status="done", locked_by=None, locked_at=None)
    )
    async with factory() as session, session.begin():
        return bool((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


async def release(factory: Factory, job_id: int, worker_id: str) -> bool:
    """Give a job back untouched (worker shutting down): queued again, attempt not used up."""
    stmt = (
        update(Job)
        .where(*_held_by(job_id, worker_id))
        .values(
            status="queued",
            attempts=case((Job.attempts > 0, Job.attempts - 1), else_=0),
            locked_by=None,
            locked_at=None,
        )
    )
    async with factory() as session, session.begin():
        return bool((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


async def fail(factory: Factory, job: ClaimedJob, worker_id: str, error: str) -> bool:
    """Record a failed attempt: retry with backoff while attempts remain, otherwise final."""
    retry = job.attempts < job.max_attempts
    values: dict[str, Any] = {
        "status": "queued" if retry else "failed",
        "locked_by": None,
        "locked_at": None,
        "last_error": error[:MAX_ERROR_CHARS],
    }
    if retry:
        values["run_after"] = datetime.now(UTC) + timedelta(seconds=backoff_s(job.attempts))
    stmt = update(Job).where(*_held_by(job.id, worker_id)).values(**values)
    async with factory() as session, session.begin():
        return bool((await session.execute(stmt)).rowcount)  # type: ignore[attr-defined]


async def requeue_stale(factory: Factory, *, older_than: timedelta) -> int:
    """Return `running` jobs with no heartbeat for `older_than` to the queue (their worker died).
    A job that has used all its attempts fails instead of looping forever."""
    stmt = (
        update(Job)
        .where(Job.status == "running", Job.locked_at < func.now() - older_than)
        .values(
            status=case((Job.attempts >= Job.max_attempts, "failed"), else_="queued"),
            locked_by=None,
            locked_at=None,
            last_error="worker stopped sending heartbeats",
        )
        .returning(Job.id)
    )
    async with factory() as session, session.begin():
        return len((await session.execute(stmt)).all())
