"""Rate limiting helpers over the Postgres fixed-window counters.

Pattern for abuse-prone endpoints: `check` before doing the work (raises 429 with Retry-After when
the window is exhausted), `record` for each *failed* attempt. Failures are recorded in their own
short transaction so they persist even though the request then returns an error.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError
from app.db.repositories import rate_limits

LOGIN_FAILURES_PER_WINDOW = 10  # per IP + email
INVITE_FAILURES_PER_WINDOW = 10  # per IP
WINDOW_S = 15 * 60


def window_start(now: datetime, window_s: int) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % window_s, tz=UTC)


def retry_after_s(now: datetime, window_s: int) -> int:
    start = window_start(now, window_s)
    return max(1, int(start.timestamp()) + window_s - int(now.timestamp()))


def rate_limited(
    now: datetime, window_s: int = WINDOW_S, message: str = "Too many attempts. Try again later."
) -> ApiError:
    return ApiError(
        429,
        "RATE_LIMITED",
        message,
        headers={"Retry-After": str(retry_after_s(now, window_s))},
    )


async def check(
    factory: async_sessionmaker[AsyncSession],
    key: str,
    limit: int,
    *,
    now: datetime,
    window_s: int = WINDOW_S,
) -> None:
    async with factory() as session:
        used = await rate_limits.count(session, key, window_start(now, window_s))
    if used >= limit:
        raise rate_limited(now, window_s)


async def record(
    factory: async_sessionmaker[AsyncSession],
    key: str,
    *,
    now: datetime,
    window_s: int = WINDOW_S,
) -> int:
    async with factory() as session, session.begin():
        return await rate_limits.increment(session, key, window_start(now, window_s))


async def hit(
    factory: async_sessionmaker[AsyncSession],
    key: str,
    limit: int,
    *,
    now: datetime,
    window_s: int,
    message: str = "Too many attempts. Try again later.",
) -> None:
    """Count one attempt and refuse it when it is past `limit` in the current window.

    Unlike `check` + `record`, the count and the comparison are one atomic statement, so a burst of
    parallel requests cannot all slip under the limit. Refused attempts are counted too, which is
    harmless for a fixed window. The count commits on its own so it survives the request failing.
    """
    used = await record(factory, key, now=now, window_s=window_s)
    if used > limit:
        raise rate_limited(now, window_s, message)
