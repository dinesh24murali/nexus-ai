"""Fixtures for the RunService tests: a service over a real Postgres and the scripted fake."""

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

import psycopg
import pytest
from app.core.config import Settings
from app.graph.builder import build_graph
from app.llm.fake import FakeChatModel
from app.services.runs import RunService
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db_support import libpq
from tests.services.support import Env

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def make_env(
    session_factory: Factory,
    settings: Settings,
    db_url: str,
    db: psycopg.Connection[Any],
    make_user: Any,
) -> AsyncIterator[Callable[..., Any]]:
    stack: list[Any] = []

    async def make(
        *,
        chat: Any = None,
        token_delay_s: float = 0.0,
        heartbeat_s: float = 0.05,
        flags: Any = None,
        tracer: Any = None,
        retriever: Any = None,
        web: Any = None,
        calculator: bool = False,
        settings_update: dict[str, Any] | None = None,
    ) -> Env:
        user = UUID(make_user())
        row = db.execute(
            "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (user,)
        ).fetchone()
        assert row
        ctx = AsyncPostgresSaver.from_conn_string(libpq(db_url))
        saver = await ctx.__aenter__()
        stack.append(ctx)
        overrides: list[str | None] = []

        def graph_factory(model_override: str | None) -> Any:
            overrides.append(model_override)
            return build_graph(
                checkpointer=saver,
                chat_model=chat or FakeChatModel(role="chat", token_delay_s=token_delay_s),
                summarize_model=FakeChatModel(role="summarize"),
                retriever=retriever,
                web_searcher=web,
                calculator=calculator,
            )

        service = RunService(
            factory=session_factory,
            settings=settings.model_copy(update=settings_update or {}),
            graph_factory=graph_factory,
            flags=flags,
            tracer=tracer,
            heartbeat_s=heartbeat_s,
        )
        return Env(service, saver, db, user, UUID(str(row[0])), overrides)

    try:
        yield make
    finally:
        for ctx in stack:
            await ctx.__aexit__(None, None, None)
