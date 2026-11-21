"""Periodic tasks: the run-lease reaper, stale job requeue and housekeeping (TASK-012)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

import psycopg
import pytest
from app.db.repositories.messages import assistant_message_id
from app.jobs import periodic, queue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

Factory = async_sessionmaker[AsyncSession]


# ---------- the runner (no database) ----------


async def test_a_task_runs_immediately_then_on_its_interval() -> None:
    calls: list[float] = []
    stop = asyncio.Event()

    async def fn() -> None:
        calls.append(asyncio.get_running_loop().time())
        if len(calls) == 3:
            stop.set()

    await asyncio.wait_for(periodic.run_periodic(periodic.Task("t", 0.02, fn), stop), timeout=2)

    assert len(calls) == 3
    assert calls[1] - calls[0] >= 0.015


async def test_a_failing_task_is_logged_and_tried_again() -> None:
    attempts = 0
    stop = asyncio.Event()

    async def fn() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database hiccup")
        stop.set()

    await asyncio.wait_for(periodic.run_periodic(periodic.Task("t", 0.01, fn), stop), timeout=2)

    assert attempts == 2


async def test_the_runner_stops_promptly_when_asked() -> None:
    stop = asyncio.Event()

    async def fn() -> None:
        return None

    runner = asyncio.create_task(periodic.run_periodic(periodic.Task("t", 3600, fn), stop))
    await asyncio.sleep(0.02)
    stop.set()

    await asyncio.wait_for(runner, timeout=1)


def test_the_standard_schedule_matches_the_design() -> None:
    from app.core.config import Settings

    tasks = {t.name: t.interval_s for t in periodic.standard_tasks(Settings(_env_file=None), None)}  # type: ignore[arg-type]
    assert tasks == {
        "reap_stale_runs": 30,
        "requeue_stale_jobs": 30,
        "cleanup_invites": 86_400,
        "cleanup_rate_limits": 86_400,
    }
    assert timedelta(seconds=60) == periodic.RUN_STALE_AFTER
    assert timedelta(minutes=2) == periodic.JOB_STALE_AFTER


# ---------- reap_stale_runs ----------


def lease(
    db: psycopg.Connection[Any], user: str, *, age_s: float | None, title: str = "t"
) -> tuple[str, UUID]:
    run_id = uuid4()
    row = db.execute(
        "INSERT INTO conversations (user_id, title, active_run_id, run_heartbeat_at)"
        " VALUES (%s, %s, %s, now() - make_interval(secs => %s)) RETURNING id",
        (user, title, run_id if age_s is not None else None, age_s or 0),
    ).fetchone()
    assert row
    return str(row[0]), run_id


@pytest.mark.integration
async def test_a_stale_lease_becomes_a_failed_message_and_the_conversation_is_usable(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = make_user()
    cid, run_id = lease(db, user, age_s=61)

    reaped = await periodic.reap_stale_runs(session_factory)

    assert reaped == 1
    conv = db.execute(
        "SELECT active_run_id, run_heartbeat_at FROM conversations WHERE id = %s", (cid,)
    ).fetchone()
    assert conv == (None, None)
    msg = db.execute(
        "SELECT id, user_id, role, status, finish_reason, content FROM messages"
        " WHERE conversation_id = %s",
        (cid,),
    ).fetchall()
    assert msg == [
        (assistant_message_id(run_id), UUID(user), "assistant", "failed", "server_restart", "")
    ]


@pytest.mark.integration
async def test_a_live_lease_and_an_idle_conversation_are_left_alone(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = make_user()
    live, live_run = lease(db, user, age_s=30)
    idle, _ = lease(db, user, age_s=None)

    assert await periodic.reap_stale_runs(session_factory) == 0

    row = db.execute("SELECT active_run_id FROM conversations WHERE id = %s", (live,)).fetchone()
    assert row and row[0] == live_run
    count = db.execute(
        "SELECT count(*) FROM messages WHERE conversation_id IN (%s, %s)", (live, idle)
    ).fetchone()
    assert count and count[0] == 0


@pytest.mark.integration
async def test_the_boundary_is_sixty_seconds(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = make_user()
    lease(db, user, age_s=59)
    lease(db, user, age_s=61)

    assert await periodic.reap_stale_runs(session_factory) == 1


@pytest.mark.integration
async def test_two_workers_reaping_at_once_write_one_message(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = make_user()
    for _ in range(10):
        lease(db, user, age_s=120)

    counts = await asyncio.gather(*(periodic.reap_stale_runs(session_factory) for _ in range(4)))

    assert sum(counts) == 10
    row = db.execute("SELECT count(*) FROM messages").fetchone()
    assert row and row[0] == 10


# ---------- requeue_stale_jobs ----------


@pytest.mark.integration
async def test_jobs_without_a_heartbeat_for_two_minutes_are_requeued(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    async with session_factory() as session, session.begin():
        await queue.enqueue(session, "demo", {}, "a")
        await queue.enqueue(session, "demo", {}, "b")
    first = await queue.claim(session_factory, "w1")
    second = await queue.claim(session_factory, "w2")
    assert first and second
    db.execute(
        "UPDATE jobs SET locked_at = now() - interval '121 seconds' WHERE id = %s", (first.id,)
    )
    db.execute(
        "UPDATE jobs SET locked_at = now() - interval '119 seconds' WHERE id = %s", (second.id,)
    )

    assert await periodic.requeue_stale_jobs(session_factory) == 1


# ---------- housekeeping ----------


@pytest.mark.integration
async def test_cleanup_invites_removes_only_long_dead_unused_invites(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = make_user()
    now = datetime.now(UTC)

    def invite(
        name: str, *, expires: timedelta, used: bool = False, revoked: timedelta | None = None
    ) -> None:
        db.execute(
            "INSERT INTO invites (code_hash, note, expires_at, used_at, used_by, revoked_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                name,
                name,
                now + expires,
                now if used else None,
                user if used else None,
                now + revoked if revoked is not None else None,
            ),
        )

    invite("expired-long-ago", expires=timedelta(days=-40))
    invite("expired-recently", expires=timedelta(days=-5))
    invite("revoked-long-ago", expires=timedelta(days=30), revoked=timedelta(days=-40))
    invite("used-long-ago", expires=timedelta(days=-40), used=True)
    invite("still-valid", expires=timedelta(days=3))

    removed = await periodic.cleanup_invites(session_factory)

    left = {r[0] for r in db.execute("SELECT note FROM invites").fetchall()}
    assert removed == 2
    assert left == {"expired-recently", "used-long-ago", "still-valid"}


@pytest.mark.integration
async def test_cleanup_rate_limits_removes_old_windows_only(
    session_factory: Factory, db: psycopg.Connection[Any]
) -> None:
    db.execute(
        "INSERT INTO rate_limits (key, window_start, count) VALUES"
        " ('old', now() - interval '2 days', 5), ('recent', now() - interval '1 hour', 1)"
    )

    assert await periodic.cleanup_rate_limits(session_factory) == 1

    left = {r[0] for r in db.execute("SELECT key FROM rate_limits").fetchall()}
    assert left == {"recent"}


def test_the_assistant_message_id_is_derived_from_the_run_id() -> None:
    run_id = uuid4()
    assert assistant_message_id(run_id) == uuid5(run_id, "assistant")
    assert assistant_message_id(run_id) != run_id
