from collections.abc import Iterator

import pytest
from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings for tests: never reads .env, never needs real services or keys."""
    return Settings(
        _env_file=None,
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
