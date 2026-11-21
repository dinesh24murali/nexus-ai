"""Fixed-window counters in Postgres (no Redis in v1). Rows older than a window are dead weight; a
periodic cleanup job (TASK-012) deletes them."""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateLimit


async def count(session: AsyncSession, key: str, window_start: datetime) -> int:
    stmt = select(RateLimit.count).where(
        RateLimit.key == key, RateLimit.window_start == window_start
    )
    return (await session.execute(stmt)).scalar_one_or_none() or 0


async def increment(session: AsyncSession, key: str, window_start: datetime) -> int:
    stmt = (
        insert(RateLimit)
        .values(key=key, window_start=window_start, count=1)
        .on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window_start],
            set_={"count": RateLimit.count + 1},
        )
        .returning(RateLimit.count)
    )
    return (await session.execute(stmt)).scalar_one()


async def delete_older_than(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(delete(RateLimit).where(RateLimit.window_start < cutoff))
    return int(result.rowcount)  # type: ignore[attr-defined]
