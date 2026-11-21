"""Per-user daily spend (`usage_daily`, USD): written at run/job end, read by the budget checks."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageDaily


async def add(
    session: AsyncSession,
    user_id: uuid.UUID,
    day: date,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: Decimal,
    requests: int = 0,
) -> None:
    """Add to the user's totals for `day` (UTC). Background jobs pass `requests=0`: they spend
    tokens but are not user requests."""
    stmt = insert(UsageDaily).values(
        user_id=user_id,
        day=day,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        requests=requests,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UsageDaily.user_id, UsageDaily.day],
        set_={
            "prompt_tokens": UsageDaily.prompt_tokens + stmt.excluded.prompt_tokens,
            "completion_tokens": UsageDaily.completion_tokens + stmt.excluded.completion_tokens,
            "cost_usd": UsageDaily.cost_usd + stmt.excluded.cost_usd,
            "requests": UsageDaily.requests + stmt.excluded.requests,
        },
    )
    await session.execute(stmt)


async def get_day(session: AsyncSession, user_id: uuid.UUID, day: date) -> UsageDaily | None:
    stmt = select(UsageDaily).where(UsageDaily.user_id == user_id, UsageDaily.day == day)
    return (await session.execute(stmt)).scalar_one_or_none()


async def global_cost(session: AsyncSession, day: date) -> Decimal:
    """Spend of every user on `day` (the instance-wide cap compares against this)."""
    stmt = select(func.coalesce(func.sum(UsageDaily.cost_usd), 0)).where(UsageDaily.day == day)
    return Decimal((await session.execute(stmt)).scalar_one())
