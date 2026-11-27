"""`ratelimit.hit`: the atomic "count this attempt, refuse past the limit" used for chat sends."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.core import ratelimit
from app.core.errors import ApiError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 21, 12, 0, 20, tzinfo=UTC)


async def test_attempts_up_to_the_limit_pass_and_the_next_is_refused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(3):
        await ratelimit.hit(session_factory, "chat:u1", 3, now=NOW, window_s=60)

    with pytest.raises(ApiError) as caught:
        await ratelimit.hit(session_factory, "chat:u1", 3, now=NOW, window_s=60)

    err = caught.value
    assert (err.status_code, err.code) == (429, "RATE_LIMITED")
    assert err.headers == {"Retry-After": "40"}  # the window ends at 12:01:00


async def test_each_key_has_its_own_budget(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ratelimit.hit(session_factory, "chat:u1", 1, now=NOW, window_s=60)
    await ratelimit.hit(session_factory, "chat:u2", 1, now=NOW, window_s=60)


async def test_the_next_window_starts_fresh(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ratelimit.hit(session_factory, "chat:u1", 1, now=NOW, window_s=60)

    await ratelimit.hit(session_factory, "chat:u1", 1, now=NOW + timedelta(seconds=41), window_s=60)


async def test_a_parallel_burst_cannot_get_past_the_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def attempt() -> bool:
        try:
            await ratelimit.hit(session_factory, "chat:burst", 10, now=NOW, window_s=60)
        except ApiError:
            return False
        return True

    results = await asyncio.gather(*(attempt() for _ in range(40)))

    assert sum(results) == 10
