"""Async engine and session factory (SQLAlchemy 2 + psycopg 3; psycopg is the only driver)."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: returned ORM objects stay readable after commit (no implicit lazy IO).
    return async_sessionmaker(engine, expire_on_commit=False)
