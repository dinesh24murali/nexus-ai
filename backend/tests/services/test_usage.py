"""Budget checks, the usage summary and the 80 % spend alert, against a real Postgres."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import structlog
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.repositories import usage as usage_repo
from app.services import usage as usage_service
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 21, 15, 30, tzinfo=UTC)
DAY = date(2026, 11, 21)


@pytest.fixture
def limits(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"user_daily_budget_usd": Decimal("0.50"), "global_daily_budget_usd": Decimal("5")}
    )


async def spend(
    factory: async_sessionmaker[AsyncSession],
    user: str,
    cost: str,
    *,
    day: date = DAY,
    requests: int = 1,
) -> None:
    async with factory() as session, session.begin():
        await usage_repo.add(
            session,
            UUID(user),
            day,
            prompt_tokens=100,
            completion_tokens=20,
            cost_usd=Decimal(cost),
            requests=requests,
        )


async def test_a_user_under_both_budgets_may_start_a_run(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    user = make_user()
    await spend(session_factory, user, "0.49")

    await usage_service.ensure_within_budgets(session_factory, limits, UUID(user), now=NOW)


async def test_yesterdays_spend_does_not_count(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    user = make_user()
    await spend(session_factory, user, "9", day=date(2026, 11, 20))

    await usage_service.ensure_within_budgets(session_factory, limits, UUID(user), now=NOW)


async def test_a_user_over_their_budget_is_refused_with_scope_user(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    user = make_user()
    await spend(session_factory, user, "0.50")

    with pytest.raises(ApiError) as caught:
        await usage_service.ensure_within_budgets(session_factory, limits, UUID(user), now=NOW)

    err = caught.value
    assert (err.status_code, err.code) == (429, "BUDGET_EXCEEDED")
    assert err.details == {"scope": "user", "resets_at": "2026-11-22T00:00:00Z"}
    assert err.headers == {"Retry-After": str(8 * 3600 + 30 * 60)}


async def test_another_users_spend_does_not_block_me_until_the_global_cap(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    heavy, me = make_user("heavy@example.com"), make_user("me@example.com")
    await spend(session_factory, heavy, "0.60")  # over their own budget, far under the cap

    await usage_service.ensure_within_budgets(session_factory, limits, UUID(me), now=NOW)


async def test_the_global_cap_blocks_everyone_with_scope_global(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    me = make_user("me@example.com")
    for i in range(10):  # ten users at $0.50 = the $5 cap
        await spend(session_factory, make_user(f"u{i}@example.com"), "0.50")

    with pytest.raises(ApiError) as caught:
        await usage_service.ensure_within_budgets(session_factory, limits, UUID(me), now=NOW)

    assert caught.value.details is not None
    assert caught.value.details["scope"] == "global"


async def test_the_summary_reports_todays_numbers_for_this_user_only(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    me, other = make_user("me@example.com"), make_user("other@example.com")
    await spend(session_factory, me, "0.142", requests=3)
    await spend(session_factory, other, "1", requests=7)

    summary = await usage_service.usage_summary(session_factory, limits, UUID(me), now=NOW)

    assert summary.day == DAY
    assert summary.spend_usd == Decimal("0.142")
    assert summary.budget_usd == Decimal("0.50")
    assert summary.tokens_used == 120
    assert summary.messages == 3
    assert summary.global_cap_reached is False
    assert summary.resets_at == datetime(2026, 11, 22, tzinfo=UTC)


async def test_the_summary_for_a_user_with_no_spend_is_all_zero(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    summary = await usage_service.usage_summary(session_factory, limits, UUID(make_user()), now=NOW)

    assert (summary.spend_usd, summary.tokens_used, summary.messages) == (Decimal(0), 0, 0)


async def test_the_summary_says_when_the_global_cap_is_reached(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    me = make_user("me@example.com")
    for i in range(10):
        await spend(session_factory, make_user(f"u{i}@example.com"), "0.50")

    summary = await usage_service.usage_summary(session_factory, limits, UUID(me), now=NOW)

    assert summary.global_cap_reached is True


async def test_crossing_80_percent_of_the_cap_logs_one_alert_per_day(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    for i in range(8):  # $4.00 = exactly 80 % of $5
        await spend(session_factory, make_user(f"u{i}@example.com"), "0.50")

    with structlog.testing.capture_logs() as logs:
        await usage_service.note_global_spend(session_factory, limits, now=NOW)
        await usage_service.note_global_spend(session_factory, limits, now=NOW)

    alerts = [entry for entry in logs if entry["event"] == "global_spend_alert"]
    assert len(alerts) == 1
    assert alerts[0]["log_level"] == "warning"
    assert alerts[0]["day"] == "2026-11-21"
    assert alerts[0]["cap_usd"] == "5"


async def test_no_alert_below_80_percent(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    await spend(session_factory, make_user(), "3.99")

    with structlog.testing.capture_logs() as logs:
        await usage_service.note_global_spend(session_factory, limits, now=NOW)

    assert [e for e in logs if e["event"] == "global_spend_alert"] == []


async def test_the_next_day_can_alert_again(
    session_factory: async_sessionmaker[AsyncSession], limits: Settings, make_user: Any
) -> None:
    user = make_user()
    await spend(session_factory, user, "4.50")
    await spend(session_factory, user, "4.50", day=date(2026, 11, 22))

    with structlog.testing.capture_logs() as logs:
        await usage_service.note_global_spend(session_factory, limits, now=NOW)
        await usage_service.note_global_spend(
            session_factory, limits, now=datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
        )

    assert len([e for e in logs if e["event"] == "global_spend_alert"]) == 2
