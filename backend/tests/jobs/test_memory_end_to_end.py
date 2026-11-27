"""TASK-025 acceptance, end to end: a real send over HTTP queues `extract_memories`, a real worker
(default registry, fake LLM and embeddings) runs it, and the memory is in the store. Memory off
means no job and no memory."""

import asyncio
from pathlib import Path
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.jobs.handlers import JobContext
from app.worker import WorkerConfig, run
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.test_auth import API
from tests.api.test_conversations import auth

pytestmark = pytest.mark.integration


def stored(db: psycopg.Connection[Any]) -> list[str]:
    rows = db.execute(
        "SELECT value->>'content' FROM store WHERE prefix LIKE 'memories.%' ORDER BY created_at"
    ).fetchall()
    return [r[0] for r in rows]


def extract_jobs(db: psycopg.Connection[Any]) -> list[tuple[str, str]]:
    rows = db.execute(
        "SELECT status, idempotency_key FROM jobs WHERE kind = 'extract_memories' ORDER BY id"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


async def work_until_idle(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    stop = asyncio.Event()
    worker = asyncio.create_task(
        run(
            stop,
            heartbeat_path=tmp_path / "alive",
            interval_s=0.05,
            ctx=JobContext(factory=factory, settings=settings),
            config=WorkerConfig(poll_interval_s=0.02),
            tasks=[],
            name="e2e",
        )
    )
    try:
        for _ in range(300):
            row = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()
            if row == (0,):
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=10)


def send(client: TestClient, headers: dict[str, str], cid: str, text: str) -> None:
    client.post(f"{API}/conversations/{cid}/messages", headers=headers, json={"content": text})


@pytest.fixture
def job_settings(settings: Settings, db_url: str) -> Settings:
    return settings.model_copy(update={"database_url": db_url})


async def test_remember_that_i_prefer_go_leads_to_exactly_one_go_memory(
    client: TestClient,
    make_user: Any,
    job_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    headers = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]

    send(client, headers, cid, "Remember that I prefer Go for backend work.")
    assert len(extract_jobs(db)) == 1  # queued with the run, in its transaction
    await work_until_idle(job_settings, session_factory, db, tmp_path)

    assert stored(db) == ["User prefers Go for backend work"]
    assert [s for s, _ in extract_jobs(db)] == ["done"]


async def test_each_turn_queues_one_job_keyed_by_its_answer(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    headers = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]

    send(client, headers, cid, "first")
    send(client, headers, cid, "second")

    jobs = extract_jobs(db)
    assert len(jobs) == 2
    assert len({key for _, key in jobs}) == 2
    assert all(key.startswith("mem:") for _, key in jobs)


async def test_a_regenerated_answer_is_not_read_again(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    headers = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]
    send(client, headers, cid, "Remember that I prefer Go.")
    assert len(extract_jobs(db)) == 1

    client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)

    assert len(extract_jobs(db)) == 1


async def test_with_memory_off_no_job_is_queued_and_nothing_is_stored(
    client: TestClient,
    make_user: Any,
    job_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    headers = auth(client, make_user)
    client.patch(f"{API}/me", headers=headers, json={"memory_enabled": False})
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]

    send(client, headers, cid, "Remember that I prefer Go for backend work.")
    await work_until_idle(job_settings, session_factory, db, tmp_path)

    assert extract_jobs(db) == []
    assert stored(db) == []


async def test_turning_memory_off_after_the_job_was_queued_still_stores_nothing(
    client: TestClient,
    make_user: Any,
    job_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    headers = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]
    send(client, headers, cid, "Remember that I prefer Go for backend work.")
    assert len(extract_jobs(db)) == 1

    client.patch(f"{API}/me", headers=headers, json={"memory_enabled": False})  # before the worker
    await work_until_idle(job_settings, session_factory, db, tmp_path)

    assert stored(db) == []
