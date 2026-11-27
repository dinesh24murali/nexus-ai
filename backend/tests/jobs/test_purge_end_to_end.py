"""TASK-028 end to end: a real `DELETE /me` over HTTP, a real worker with the default job registry,
and the account, its conversation, its memory and its uploads directory are gone."""

import asyncio
from pathlib import Path
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.jobs.handlers import JobContext
from app.llm.fake import FakeEmbeddingsClient
from app.rag.embedder import Embedder
from app.rag.index import QdrantIndex
from app.worker import WorkerConfig, run
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.test_auth import API, login
from tests.api.test_conversations import auth

pytestmark = pytest.mark.integration


async def test_deleting_the_account_over_http_ends_with_everything_gone(
    client: TestClient,
    make_user: Any,
    settings: Settings,
    db_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    qdrant_index: QdrantIndex,
    db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    job_settings = settings.model_copy(update={"database_url": db_url})
    headers = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]
    client.post(
        f"{API}/conversations/{cid}/messages",
        headers=headers,
        json={"content": "Remember that I prefer Go for backend work."},
    )
    uploads = job_settings.uploads_dir
    (uploads / "someone").mkdir(parents=True, exist_ok=True)

    body = {"confirm_email": "marco@example.com"}
    assert client.request("DELETE", f"{API}/me", headers=headers, json=body).status_code == 202

    stop = asyncio.Event()
    worker = asyncio.create_task(
        run(
            stop,
            heartbeat_path=tmp_path / "alive",
            interval_s=0.05,
            ctx=JobContext(
                factory=session_factory,
                settings=job_settings,
                embedder=Embedder(FakeEmbeddingsClient(), job_settings.llm_embedding_model),
                index=qdrant_index,
            ),
            config=WorkerConfig(poll_interval_s=0.02),
            tasks=[],
            name="e2e",
        )
    )
    try:
        for _ in range(300):
            if db.execute("SELECT count(*) FROM users").fetchone() == (0,):
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=10)

    assert db.execute("SELECT count(*) FROM users").fetchone() == (0,)
    for table in ("conversations", "messages", "refresh_tokens", "usage_daily"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,), table  # noqa: S608
    assert db.execute("SELECT count(*) FROM store WHERE prefix LIKE 'memories.%'").fetchone() == (
        0,
    )
    assert db.execute(
        "SELECT count(*) FROM jobs WHERE status IN ('queued', 'running')"
    ).fetchone() == (0,)
    assert (uploads / "someone").exists()  # only this user's directory is removed
    # And the address can be registered afresh: nothing of the old account lingers.
    make_user()
    assert login(client).status_code == 200
