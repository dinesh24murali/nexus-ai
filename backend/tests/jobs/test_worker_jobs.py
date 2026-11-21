"""The worker loop against a real Postgres: dispatch, retries, timeouts, heartbeats, shutdown."""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.jobs import queue
from app.jobs.handlers import JobContext, JobSpec
from app.jobs.periodic import Task
from app.jobs.queue import ClaimedJob
from app.worker import WorkerConfig, run
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]
Handler = Callable[[JobContext, ClaimedJob], Awaitable[None]]


def fast(**overrides: Any) -> WorkerConfig:
    base: dict[str, Any] = {
        "concurrency": 2,
        "poll_interval_s": 0.02,
        "job_heartbeat_s": 0.1,
        "shutdown_grace_s": 2.0,
    }
    return WorkerConfig(**{**base, **overrides})


@asynccontextmanager
async def worker(
    factory: Factory,
    settings: Settings,
    jobs: dict[str, JobSpec],
    tmp_path: Path,
    *,
    name: str = "w",
    config: WorkerConfig | None = None,
    tasks: list[Task] | None = None,
) -> AsyncIterator[asyncio.Event]:
    stop = asyncio.Event()
    runner = asyncio.create_task(
        run(
            stop,
            heartbeat_path=tmp_path / f"alive-{name}",
            interval_s=0.05,
            ctx=JobContext(factory=factory, settings=settings),
            jobs=jobs,
            config=config or fast(),
            tasks=tasks if tasks is not None else [],
            name=name,
        )
    )
    try:
        yield stop
    finally:
        stop.set()
        await asyncio.wait_for(runner, timeout=10)


async def put(factory: Factory, key: str, kind: str = "demo", **kw: Any) -> None:
    async with factory() as session, session.begin():
        await queue.enqueue(session, kind, {"n": key}, key, **kw)


async def until(check: Callable[[], bool], within_s: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + within_s
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached in time"
        await asyncio.sleep(0.02)


def statuses(db: psycopg.Connection[Any]) -> dict[str, str]:
    return {r[0]: r[1] for r in db.execute("SELECT idempotency_key, status FROM jobs").fetchall()}


def spec(handler: Handler, timeout_s: float = 5, max_attempts: int = 3) -> JobSpec:
    return JobSpec(handler, timeout_s=timeout_s, max_attempts=max_attempts)


async def test_a_queued_job_is_run_and_marked_done(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        seen.append(job.payload)

    await put(session_factory, "a")
    async with worker(session_factory, settings, {"demo": spec(handler)}, tmp_path):
        await until(lambda: statuses(db) == {"a": "done"})

    assert seen == [{"n": "a"}]


async def test_a_failing_job_is_retried_with_backoff_then_failed(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        raise RuntimeError("model unavailable")

    await put(session_factory, "a", max_attempts=2)
    async with worker(session_factory, settings, {"demo": spec(handler, max_attempts=2)}, tmp_path):
        await until(lambda: db.execute("SELECT attempts FROM jobs").fetchone() == (1,))
        await until(lambda: statuses(db) == {"a": "queued"})  # attempt 1 failed, backoff pending
        row = db.execute("SELECT attempts, last_error FROM jobs").fetchone()
        assert row == (1, "RuntimeError: model unavailable")
        db.execute("UPDATE jobs SET run_after = now()")  # skip the 10 s wait
        await until(lambda: statuses(db) == {"a": "failed"})

    row = db.execute("SELECT attempts FROM jobs").fetchone()
    assert row == (2,)


async def test_a_job_past_its_hard_timeout_fails_the_attempt(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    cancelled = asyncio.Event()

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    await put(session_factory, "a", max_attempts=1)
    async with worker(
        session_factory, settings, {"demo": spec(handler, timeout_s=0.2, max_attempts=1)}, tmp_path
    ):
        await until(lambda: statuses(db) == {"a": "failed"})

    assert cancelled.is_set()
    row = db.execute("SELECT last_error FROM jobs").fetchone()
    assert row and "timed out" in row[0]


async def test_kinds_without_a_handler_are_left_in_the_queue(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        return None

    await put(session_factory, "known")
    await put(session_factory, "mystery", kind="from-the-future")
    async with worker(session_factory, settings, {"demo": spec(handler)}, tmp_path):
        await until(lambda: statuses(db)["known"] == "done")
        await asyncio.sleep(0.2)

    assert statuses(db)["mystery"] == "queued"


async def test_two_workers_never_process_the_same_job(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    runs: Counter[str] = Counter()

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        runs[job.payload["n"]] += 1
        await asyncio.sleep(0.01)

    for i in range(40):
        await put(session_factory, f"job-{i}")
    jobs = {"demo": spec(handler)}
    async with (
        worker(session_factory, settings, jobs, tmp_path, name="a"),
        worker(session_factory, settings, jobs, tmp_path, name="b"),
    ):
        await until(lambda: set(statuses(db).values()) == {"done"} and len(statuses(db)) == 40)

    assert set(runs.values()) == {1}
    assert len(runs) == 40


def sweep(factory: Factory, stale_after_s: float) -> list[Task]:
    return [
        Task(
            "requeue_stale_jobs",
            0.1,
            lambda: queue.requeue_stale(factory, older_than=timedelta(seconds=stale_after_s)),
        )
    ]


async def test_a_job_that_outlives_the_stale_window_is_not_picked_up_twice(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    """Scaled time: production is a 30 s heartbeat and a 2 min stale window with a job longer than
    5 min; here it is a 0.1 s heartbeat, a 0.5 s window and a 1.5 s job. The property is the same:
    the heartbeat keeps a long job from being requeued while its worker is alive."""
    runs: Counter[str] = Counter()

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        runs["long"] += 1
        await asyncio.sleep(1.5)

    jobs = {"demo": spec(handler, timeout_s=10)}
    await put(session_factory, "long")
    tasks = sweep(session_factory, 0.5)
    async with (
        worker(session_factory, settings, jobs, tmp_path, name="a", tasks=tasks),
        worker(session_factory, settings, jobs, tmp_path, name="b", tasks=tasks),
    ):
        await until(lambda: statuses(db) == {"long": "done"})

    assert runs["long"] == 1


async def test_without_a_heartbeat_the_same_job_would_run_twice(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    """Negative control for the test above: it must be able to fail."""
    runs: Counter[str] = Counter()

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        runs["long"] += 1
        await asyncio.sleep(1.5)

    jobs = {"demo": spec(handler, timeout_s=10)}
    await put(session_factory, "long")
    tasks = sweep(session_factory, 0.5)
    quiet = fast(job_heartbeat_s=3600)  # the heartbeat never fires
    async with (
        worker(session_factory, settings, jobs, tmp_path, name="a", config=quiet, tasks=tasks),
        worker(session_factory, settings, jobs, tmp_path, name="b", config=quiet, tasks=tasks),
    ):
        await until(lambda: runs["long"] >= 2)


async def test_a_worker_that_lost_its_job_stops_working_on_it(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await put(session_factory, "a")
    async with worker(session_factory, settings, {"demo": spec(handler, timeout_s=60)}, tmp_path):
        await asyncio.wait_for(started.wait(), 5)
        db.execute(
            "UPDATE jobs SET locked_by = 'someone-else'"
        )  # requeued and re-claimed elsewhere
        await asyncio.wait_for(cancelled.wait(), 5)

    row = db.execute("SELECT status, locked_by FROM jobs").fetchone()
    assert row == ("running", "someone-else")  # untouched: it is no longer ours


async def test_shutdown_lets_a_running_job_finish(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    started = asyncio.Event()

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        started.set()
        await asyncio.sleep(0.4)

    await put(session_factory, "a")
    await put(session_factory, "later")
    async with worker(
        session_factory, settings, {"demo": spec(handler)}, tmp_path, config=fast(concurrency=1)
    ) as stop:
        await asyncio.wait_for(started.wait(), 5)
        stop.set()

    assert statuses(db)["a"] == "done"  # finished despite the stop signal
    assert statuses(db)["later"] == "queued"  # nothing new was claimed after it


async def test_a_job_still_running_after_the_grace_period_goes_back_to_the_queue(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], tmp_path: Path
) -> None:
    started = asyncio.Event()

    async def handler(ctx: JobContext, job: ClaimedJob) -> None:
        started.set()
        await asyncio.sleep(30)

    await put(session_factory, "a")
    jobs = {"demo": spec(handler, timeout_s=60)}
    async with worker(
        session_factory, settings, jobs, tmp_path, config=fast(shutdown_grace_s=0.2)
    ) as stop:
        await asyncio.wait_for(started.wait(), 5)
        stop.set()

    row = db.execute("SELECT status, attempts, locked_by FROM jobs").fetchone()
    assert row == ("queued", 0, None)  # released without using up an attempt
