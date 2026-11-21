from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import pytest
from app.db.repositories import usage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


async def test_usage_accumulates_per_user_and_day(
    session_factory: async_sessionmaker[AsyncSession],
    db: psycopg.Connection[Any],
    make_user: Any,
) -> None:
    user = UUID(make_user())
    day = date(2026, 11, 21)

    async with session_factory() as session, session.begin():
        await usage.add(
            session,
            user,
            day,
            prompt_tokens=100,
            completion_tokens=10,
            cost_usd=Decimal("0.001"),
            requests=1,
        )
    async with session_factory() as session, session.begin():
        await usage.add(
            session,
            user,
            day,
            prompt_tokens=50,
            completion_tokens=5,
            cost_usd=Decimal("0.0005"),
            requests=0,
        )
        await usage.add(
            session,
            user,
            date(2026, 11, 22),
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=Decimal("0.1"),
            requests=1,
        )

    rows = db.execute(
        "SELECT day, prompt_tokens, completion_tokens, cost_usd, requests FROM usage_daily"
        " WHERE user_id = %s ORDER BY day",
        (user,),
    ).fetchall()
    assert rows == [
        (day, 150, 15, Decimal("0.001500"), 1),
        (date(2026, 11, 22), 1, 1, Decimal("0.100000"), 1),
    ]
