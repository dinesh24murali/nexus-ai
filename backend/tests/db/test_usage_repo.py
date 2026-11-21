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


async def test_reading_a_users_day_and_the_global_total(
    session_factory: async_sessionmaker[AsyncSession], make_user: Any
) -> None:
    alice, bob = UUID(make_user("a@example.com")), UUID(make_user("b@example.com"))
    day = date(2026, 11, 21)
    async with session_factory() as session, session.begin():
        for user, cost, tokens in ((alice, "0.25", 100), (bob, "0.10", 40)):
            await usage.add(
                session,
                user,
                day,
                prompt_tokens=tokens,
                completion_tokens=10,
                cost_usd=Decimal(cost),
                requests=2,
            )
        await usage.add(
            session,
            alice,
            date(2026, 11, 20),
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=Decimal("3"),
            requests=1,
        )

    async with session_factory() as session:
        mine = await usage.get_day(session, alice, day)
        nobody = await usage.get_day(session, alice, date(2026, 11, 19))
        total = await usage.global_cost(session, day)
        empty = await usage.global_cost(session, date(2026, 11, 19))

    assert mine is not None
    assert (mine.cost_usd, mine.prompt_tokens + mine.completion_tokens, mine.requests) == (
        Decimal("0.25"),
        110,
        2,
    )
    assert nobody is None
    assert total == Decimal("0.35")  # both users, that day only
    assert empty == Decimal(0)
