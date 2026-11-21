"""Helpers for tests that need a REAL Postgres (never mocked; AGENTS.md).

`TEST_DATABASE_URL` points at the Compose Postgres (`make up`). Every test gets its own database,
cloned from a migrated template, and dropped afterwards, so tests never touch dev data.
"""

import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

DEFAULT_URL = "postgresql+psycopg://nexus:nexus@localhost:5432/nexus"


def admin_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)


def libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def url_for(database: str) -> str:
    return make_url(admin_url()).set(database=database).render_as_string(hide_password=False)


def _admin_conn() -> psycopg.Connection[tuple[Any, ...]]:
    return psycopg.connect(libpq(url_for("postgres")), autocommit=True)


@contextmanager
def database(*, template: str | None = None) -> Iterator[str]:
    """Create a throwaway database (optionally cloned from `template`); yield its URL; drop it."""
    name = f"nexus_test_{secrets.token_hex(6)}"
    stmt = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template:
        stmt = sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(template)
        )
    with _admin_conn() as conn:
        conn.execute(stmt)
    try:
        yield url_for(name)
    finally:
        with _admin_conn() as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def database_name(url: str) -> str:
    name = make_url(url).database
    assert name
    return name
