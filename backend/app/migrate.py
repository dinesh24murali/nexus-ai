"""Migrate step (`python -m app.migrate`): runs before the api and worker start.

1. Alembic `upgrade head` for the application tables.
2. LangGraph `checkpointer.setup()` and `store.setup()` (their own idempotent migrations).
3. The Qdrant collection for document chunks (`app.rag.index`).

Idempotent: running it again is a no-op. Exits non-zero on any failure so Compose keeps the api and
worker from starting against a half-migrated database.
"""

import asyncio
import sys
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from qdrant_client import AsyncQdrantClient

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.rag.index import QdrantIndex

_BACKEND_DIR = Path(__file__).resolve().parents[1]

_log = structlog.get_logger("nexus.migrate")


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def run_alembic(database_url: str) -> None:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    cfg.attributes["configure_logging"] = False  # keep our JSON logging
    command.upgrade(cfg, "head")


async def setup_langgraph(database_url: str) -> None:
    conn = _libpq(database_url)
    async with AsyncPostgresSaver.from_conn_string(conn) as saver:
        await saver.setup()
    async with AsyncPostgresStore.from_conn_string(conn) as store:
        await store.setup()


async def setup_qdrant(settings: Settings) -> None:
    client = AsyncQdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() or None
    )
    try:
        await QdrantIndex(client, alias=settings.qdrant_collection).ensure_collection()
    finally:
        await client.close()


def migrate(database_url: str) -> None:
    _log.info("migrate_started")
    run_alembic(database_url)
    asyncio.run(setup_langgraph(database_url))
    _log.info("migrate_finished")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        migrate(settings.database_url)
        asyncio.run(setup_qdrant(settings))
    except Exception:
        _log.exception("migrate_failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
