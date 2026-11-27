"""TASK-011 acceptance: killing the API mid-run leaves the conversation usable after the reaper.

The API runs as a real subprocess and is SIGKILLed while streaming (no graceful drain, no cleanup),
the way an OOM kill or a crash would end it.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from app.core.config import Settings
from app.jobs import periodic
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_api(settings: Settings, db_url: str, port: int) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": db_url,
        "AUTH_SECRET": settings.auth_secret.get_secret_value(),
        "APP_SECRET": settings.app_secret.get_secret_value(),
        "LLM_MODE": "fake",
        "LLM_FAKE_TOKEN_DELAY_S": "0.05",
        "LANGFUSE_PUBLIC_KEY": "",  # never send traces from a test
        "LANGFUSE_SECRET_KEY": "",
    }
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, test-only
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", str(port)],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/v1/healthz", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.1)
    proc.kill()
    pytest.fail("the API subprocess did not become healthy")


async def test_a_killed_api_leaves_a_conversation_the_reaper_frees(
    settings: Settings,
    db_url: str,
    db: psycopg.Connection[Any],
    make_user: Any,
    session_factory: async_sessionmaker[AsyncSession],
    live_server: Callable[..., str],
) -> None:
    make_user()
    port = free_port()
    proc = start_api(settings, db_url, port)
    base = f"http://127.0.0.1:{port}/api/v1"

    def stream_then_kill() -> tuple[str, int]:
        tokens = 0
        with httpx.Client(timeout=30) as http:
            token = http.post(
                f"{base}/auth/login",
                json={"email": "marco@example.com", "password": "correct-horse-battery"},
            ).json()["access_token"]
            http.headers["authorization"] = f"Bearer {token}"
            cid = http.post(f"{base}/conversations", json={}).json()["id"]
            body = {"content": "[[fixture:long]] hi"}
            with http.stream("POST", f"{base}/conversations/{cid}/messages", json=body) as r:
                for line in r.iter_lines():
                    if line.startswith("data: ") and json.loads(line[6:])["type"] == "token":
                        tokens += 1
                    if tokens == 3:
                        break
                proc.kill()  # SIGKILL mid-run: no drain, no `finally`, nothing is saved
                proc.wait(timeout=10)
        return cid, tokens

    try:
        cid, tokens = await asyncio.to_thread(stream_then_kill)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert tokens == 3

    # The dead process left the conversation locked...
    locked = db.execute("SELECT active_run_id IS NOT NULL FROM conversations").fetchone()
    assert locked == (True,)
    assert db.execute("SELECT count(*) FROM messages WHERE role = 'assistant'").fetchone() == (0,)

    # ...until the reaper sees that the heartbeat stopped (60 s in production, 1 s here).
    await asyncio.sleep(1.5)
    assert await periodic.reap_stale_runs(session_factory, stale_after=timedelta(seconds=1)) == 1

    row = db.execute(
        "SELECT status, finish_reason, content FROM messages WHERE role = 'assistant'"
    ).fetchone()
    assert row == ("failed", "server_restart", "")
    assert db.execute("SELECT active_run_id FROM conversations").fetchone() == (None,)

    # A fresh API instance can now talk in that conversation.
    fresh = live_server()

    def talk_again() -> httpx.Response:
        with httpx.Client(timeout=30) as http:
            token = http.post(
                f"{fresh}/auth/login",
                json={"email": "marco@example.com", "password": "correct-horse-battery"},
            ).json()["access_token"]
            return http.post(
                f"{fresh}/conversations/{cid}/messages",
                headers={"authorization": f"Bearer {token}"},
                json={"content": "are you back?"},
            )

    r = await asyncio.to_thread(talk_again)
    assert r.status_code == 200 and '"finish_reason":"stop"' in r.text
    statuses = db.execute("SELECT role, status FROM messages ORDER BY created_at, id").fetchall()
    assert statuses == [
        ("user", "complete"),
        ("assistant", "failed"),
        ("user", "complete"),
        ("assistant", "complete"),
    ]
