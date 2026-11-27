"""The Postgres job queue (ADR-0007) against a real database."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from app.db.models import Job
from app.jobs import queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


async def put(factory: Factory, key: str = "k1", kind: str = "demo", **kw: Any) -> int | None:
    async with factory() as session, session.begin():
        return await queue.enqueue(session, kind, kw.pop("payload", {}), key, **kw)


def row(db: psycopg.Connection[Any], job_id: int) -> dict[str, Any]:
    cur = db.execute(
        "SELECT status, attempts, max_attempts, locked_by, locked_at, run_after, last_error"
        " FROM jobs WHERE id = %s",
        (job_id,),
    )
    values = cur.fetchone()
    assert values
    return dict(zip([c.name for c in cur.description or []], values, strict=True))


async def test_enqueue_is_idempotent_on_the_key(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    first = await put(session_factory, "title:1")
    second = await put(session_factory, "title:1")

    assert first is not None
    assert second is None
    count = db.execute("SELECT count(*) FROM jobs").fetchone()
    assert count and count[0] == 1


async def test_enqueue_joins_the_callers_transaction(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    async with session_factory() as session, session.begin():
        await queue.enqueue(session, "demo", {}, "rolled-back")
        await session.rollback()
    count = db.execute("SELECT count(*) FROM jobs").fetchone()
    assert count and count[0] == 0


async def test_claim_marks_the_job_running_for_the_worker(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    job_id = await put(session_factory, payload={"conversation_id": "c1"})

    job = await queue.claim(session_factory, "w1")

    assert job is not None
    assert (job.id, job.kind, job.payload, job.attempts) == (
        job_id,
        "demo",
        {"conversation_id": "c1"},
        1,
    )
    stored = row(db, job.id)
    assert stored["status"] == "running"
    assert stored["locked_by"] == "w1"
    assert stored["locked_at"] is not None


async def test_claim_returns_none_when_nothing_is_due(session_factory: Factory) -> None:
    assert await queue.claim(session_factory, "w1") is None
    await put(session_factory, run_after=datetime.now(UTC) + timedelta(minutes=5))
    assert await queue.claim(session_factory, "w1") is None


async def test_claim_takes_the_oldest_due_job_first(session_factory: Factory) -> None:
    now = datetime.now(UTC)
    late = await put(session_factory, "late", run_after=now - timedelta(seconds=1))
    early = await put(session_factory, "early", run_after=now - timedelta(seconds=60))

    first = await queue.claim(session_factory, "w1")
    second = await queue.claim(session_factory, "w1")

    assert first and second
    assert (first.id, second.id) == (early, late)


async def test_claim_can_be_limited_to_kinds(session_factory: Factory) -> None:
    await put(session_factory, "a", kind="other")
    wanted = await put(session_factory, "b", kind="title")

    job = await queue.claim(session_factory, "w1", kinds=["title"])

    assert job and job.id == wanted


async def test_concurrent_claims_never_hand_out_the_same_job(session_factory: Factory) -> None:
    ids = {await put(session_factory, f"k{i}") for i in range(30)}

    async def drain(worker: str) -> list[int]:
        got: list[int] = []
        while (job := await queue.claim(session_factory, worker)) is not None:
            got.append(job.id)
        return got

    results = await asyncio.gather(*(drain(f"w{i}") for i in range(6)))

    claimed = [job_id for part in results for job_id in part]
    assert sorted(claimed) == sorted(i for i in ids if i is not None)  # none twice, none missed


async def test_a_claim_skips_a_row_another_worker_is_claiming_instead_of_waiting(
    session_factory: Factory,
) -> None:
    """Deterministic SKIP LOCKED check: worker A holds the lock on the oldest job mid-claim."""
    now = datetime.now(UTC)
    first = await put(session_factory, "first", run_after=now - timedelta(seconds=60))
    second = await put(session_factory, "second", run_after=now - timedelta(seconds=30))

    async with session_factory() as holder, holder.begin():
        locked = await holder.execute(
            select(Job.id)
            .where(Job.status == "queued")
            .order_by(Job.run_after)
            .limit(1)
            .with_for_update()
        )
        assert locked.scalar_one() == first  # A is mid-claim on the oldest job

        job = await asyncio.wait_for(queue.claim(session_factory, "w-b"), timeout=5)

    assert job is not None
    assert job.id == second  # B went past A's row, and did not block on it


async def test_complete_only_for_the_worker_holding_the_job(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory)
    job = await queue.claim(session_factory, "w1")
    assert job

    assert await queue.complete(session_factory, job.id, "someone-else") is False
    assert row(db, job.id)["status"] == "running"
    assert await queue.complete(session_factory, job.id, "w1") is True
    assert row(db, job.id)["status"] == "done"


async def test_failure_with_attempts_left_requeues_with_backoff(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory, max_attempts=3)
    job = await queue.claim(session_factory, "w1")
    assert job

    before = datetime.now(UTC)
    assert await queue.fail(session_factory, job, "w1", "boom") is True

    stored = row(db, job.id)
    assert stored["status"] == "queued"
    assert stored["locked_by"] is None
    assert stored["last_error"] == "boom"
    assert stored["run_after"] >= before + timedelta(seconds=queue.backoff_s(1)) - timedelta(
        seconds=1
    )
    assert await queue.claim(session_factory, "w2") is None  # not due yet


async def test_failure_with_no_attempts_left_is_final(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory, max_attempts=1)
    job = await queue.claim(session_factory, "w1")
    assert job

    await queue.fail(session_factory, job, "w1", "boom")

    assert row(db, job.id)["status"] == "failed"


def test_backoff_doubles_from_ten_seconds_up_to_two_minutes() -> None:
    assert [queue.backoff_s(n) for n in (1, 2, 3, 4, 5, 9)] == [10, 20, 40, 80, 120, 120]


async def test_heartbeat_refreshes_locked_at_for_the_owner_only(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory)
    job = await queue.claim(session_factory, "w1")
    assert job
    db.execute("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE id = %s", (job.id,))

    assert await queue.heartbeat(session_factory, job.id, "intruder") is False
    assert row(db, job.id)["locked_at"] < datetime.now(UTC) - timedelta(minutes=30)
    assert await queue.heartbeat(session_factory, job.id, "w1") is True
    assert row(db, job.id)["locked_at"] > datetime.now(UTC) - timedelta(minutes=1)


async def test_requeue_stale_returns_silent_jobs_to_the_queue(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory, "stale")
    await put(session_factory, "fresh")
    stale = await queue.claim(session_factory, "w1")
    fresh = await queue.claim(session_factory, "w2")
    assert stale and fresh
    db.execute(
        "UPDATE jobs SET locked_at = now() - interval '3 minutes' WHERE id = %s", (stale.id,)
    )

    count = await queue.requeue_stale(session_factory, older_than=timedelta(minutes=2))

    assert count == 1
    assert row(db, stale.id)["status"] == "queued"
    assert row(db, stale.id)["locked_by"] is None
    assert row(db, fresh.id)["status"] == "running"


async def test_requeue_stale_fails_a_job_that_has_used_all_its_attempts(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory, max_attempts=1)
    job = await queue.claim(session_factory, "w1")
    assert job
    db.execute("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE id = %s", (job.id,))

    await queue.requeue_stale(session_factory, older_than=timedelta(minutes=2))

    assert row(db, job.id)["status"] == "failed"
    assert "heartbeat" in (row(db, job.id)["last_error"] or "")


async def test_a_worker_that_lost_its_job_cannot_complete_it(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory)
    first = await queue.claim(session_factory, "w1")
    assert first
    db.execute("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE id = %s", (first.id,))
    await queue.requeue_stale(session_factory, older_than=timedelta(minutes=2))
    second = await queue.claim(session_factory, "w2")
    assert second and second.id == first.id

    assert await queue.complete(session_factory, first.id, "w1") is False
    assert row(db, first.id)["locked_by"] == "w2"


async def test_release_returns_a_job_without_using_an_attempt(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    await put(session_factory)
    job = await queue.claim(session_factory, "w1")
    assert job

    assert await queue.release(session_factory, job.id, "w1") is True

    stored = row(db, job.id)
    assert (stored["status"], stored["attempts"], stored["locked_by"]) == ("queued", 0, None)
    assert await queue.release(session_factory, job.id, "w1") is False  # no longer held
