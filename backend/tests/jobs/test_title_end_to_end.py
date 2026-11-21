"""TASK-012 acceptance: a title appears after the first exchange.

A real send over HTTP queues the job; a real worker (default job registry, fake model) runs it.
"""

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


async def test_the_first_exchange_gets_a_title_from_the_worker(
    client: TestClient,
    make_user: Any,
    settings: Settings,
    db_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    headers = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]
    client.post(f"{API}/conversations/{cid}/messages", headers=headers, json={"content": "hello"})
    assert client.get(f"{API}/conversations/{cid}", headers=headers).json()["title"] == "New chat"

    stop = asyncio.Event()
    worker = asyncio.create_task(
        run(
            stop,
            heartbeat_path=tmp_path / "alive",
            interval_s=0.05,
            # real registry, fake LLM; the memory job (queued by the same send) needs this database
            ctx=JobContext(
                factory=session_factory,
                settings=settings.model_copy(update={"database_url": db_url}),
            ),
            config=WorkerConfig(poll_interval_s=0.02),
            tasks=[],
            name="e2e",
        )
    )
    try:
        for _ in range(200):
            title = client.get(f"{API}/conversations/{cid}", headers=headers).json()["title"]
            if title != "New chat":
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=10)

    assert title == "Fake title"
    assert db.execute("SELECT status FROM jobs WHERE kind = 'generate_title'").fetchone() == (
        "done",
    )
    # a second turn must not create a second title job (one per conversation)
    client.post(f"{API}/conversations/{cid}/messages", headers=headers, json={"content": "more"})
    assert db.execute("SELECT count(*) FROM jobs WHERE kind = 'generate_title'").fetchone() == (1,)
