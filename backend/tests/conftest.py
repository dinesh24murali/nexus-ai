from collections.abc import Iterator
from pathlib import Path

import pytest
from app.core.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings for tests: never reads .env, never needs real services or keys."""
    return Settings(
        _env_file=None,
        uploads_dir=tmp_path / "uploads",
        app_env="test",
        llm_mode="fake",
        auth_secret="test-auth-secret-0123456789abcdef0123456789abcdef",
        app_secret="test-app-secret-0123456789abcdef0123456789abcdef",
    )


@pytest.fixture(autouse=True)
def _clean_structlog() -> Iterator[None]:
    import structlog

    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


# ───────────── real-Postgres fixtures (tests marked `integration`) ─────────────
from collections.abc import Generator  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

import psycopg  # noqa: E402
from app.core.security import generate_invite_code, hash_password, hash_token  # noqa: E402
from app.main import create_app  # noqa: E402
from app.migrate import migrate  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tests.db_support import database, database_name, libpq  # noqa: E402


@pytest.fixture(scope="session")
def _migrated_template() -> Iterator[str]:
    """One fully migrated database per test session; per-test databases are cheap clones of it."""
    with database() as url:
        migrate(url)
        yield database_name(url)


@pytest.fixture
def db_url(_migrated_template: str) -> Iterator[str]:
    with database(template=_migrated_template) as url:
        yield url


@pytest.fixture
def db(db_url: str) -> Iterator[psycopg.Connection[Any]]:
    """Autocommit psycopg connection for arranging and asserting database state directly."""
    with psycopg.connect(libpq(db_url), autocommit=True) as conn:
        yield conn


@pytest.fixture
def client(settings: Settings, db_url: str) -> Generator[TestClient]:
    """API client wired to a fresh database. https base so Secure cookies behave realistically."""
    app = create_app(settings.model_copy(update={"database_url": db_url}))
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def make_invite(db: psycopg.Connection[Any]) -> Any:
    def _make(
        *,
        expires_in: timedelta = timedelta(days=7),
        used: bool = False,
        revoked: bool = False,
        note: str | None = None,
    ) -> str:
        code = generate_invite_code()
        now = datetime.now(UTC)
        db.execute(
            "INSERT INTO invites (code_hash, note, expires_at, used_at, revoked_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (
                hash_token(code),
                note,
                now + expires_in,
                now if used else None,
                now if revoked else None,
            ),
        )
        return code

    return _make


@pytest.fixture
def make_user(db: psycopg.Connection[Any]) -> Any:
    def _make(
        email: str = "marco@example.com",
        password: str = "correct-horse-battery",
        display_name: str = "Marco",
        status: str = "active",
    ) -> str:
        row = db.execute(
            "INSERT INTO users (email, password_hash, display_name, status)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (email, hash_password(password), display_name, status),
        ).fetchone()
        assert row
        return str(row[0])

    return _make


# ───────────── async SQLAlchemy sessions on a fresh database ─────────────
from collections.abc import AsyncIterator  # noqa: E402

from app.db.session import create_engine, create_session_factory  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402


@pytest.fixture
async def session_factory(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Async sessions on a fresh, migrated database (real Postgres, never mocked)."""
    engine = create_engine(db_url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


# ───────────── the real app under uvicorn, for streaming and contract tests ─────────────
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable  # noqa: E402

import uvicorn  # noqa: E402


@pytest.fixture
def live_server(settings: Settings, db_url: str) -> Iterator[Callable[..., str]]:
    """`start(**settings_overrides) -> base url`: run the app under uvicorn in a thread, so tests
    can hold a streaming connection open, drop it, and watch the server from outside."""
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(**overrides: Any) -> str:
        app = create_app(settings.model_copy(update={"database_url": db_url, **overrides}))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="on")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.02)
        servers.append((server, thread))
        return f"http://127.0.0.1:{port}/api/v1"

    try:
        yield start
    finally:
        for server, thread in servers:
            server.should_exit = True
            thread.join(timeout=15)


@pytest.fixture
def live_server_url(live_server: Callable[..., str]) -> str:
    return live_server()


# ───────────── a real Qdrant (tests marked `integration`) ─────────────
import os  # noqa: E402
import uuid  # noqa: E402

from app.rag.index import QdrantIndex  # noqa: E402
from qdrant_client import AsyncQdrantClient  # noqa: E402

from tests.rag.qdrant_support import drop  # noqa: E402

QDRANT_URL = os.environ.get("TEST_QDRANT_URL", "http://localhost:6333")


@pytest.fixture
async def qdrant_index() -> AsyncIterator[QdrantIndex]:
    """An index on its own alias, so tests never see each other's points; removed afterwards."""
    alias = f"test_documents_{uuid.uuid4().hex[:12]}"
    client = AsyncQdrantClient(url=QDRANT_URL)
    index = QdrantIndex(client, alias=alias)
    try:
        yield index
    finally:
        await drop(index)
        await client.close()
