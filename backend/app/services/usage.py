"""Spend reads for the budgets and `/me/usage` (docs/02_outputs/02 FS-008).

Spend is `usage_daily.cost_usd`, written when a run or a background job ends, so a title or a memory
extraction counts toward the user's day like a chat run does. The comparison policy lives in
`core/budgets.py`.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import ratelimit
from app.core.budgets import budget_exceeded, exceeded_scope, next_reset
from app.core.config import Settings
from app.db.models import UsageDaily
from app.db.repositories import usage as usage_repo

_log = structlog.get_logger("nexus.usage")

Factory = async_sessionmaker[AsyncSession]

ALERT_FRACTION = Decimal("0.8")  # alert at 80 % of the global cap (threat model LLM-T11)
_DAY_S = 24 * 60 * 60


def utc_day(now: datetime) -> date:
    return now.astimezone(UTC).date()


@dataclass(frozen=True)
class UsageSummary:
    day: date
    spend_usd: Decimal
    budget_usd: Decimal
    tokens_used: int
    messages: int
    global_cap_reached: bool
    resets_at: datetime


async def _spend(
    session: AsyncSession, user_id: uuid.UUID, day: date
) -> tuple[Decimal, Decimal, UsageDaily | None]:
    row = await usage_repo.get_day(session, user_id, day)
    mine = row.cost_usd if row is not None else Decimal(0)
    return mine, await usage_repo.global_cost(session, day), row


async def ensure_within_budgets(
    factory: Factory, settings: Settings, user_id: uuid.UUID, *, now: datetime
) -> None:
    """Refuse a new run (429 `BUDGET_EXCEEDED`) when the user's or the instance's day is spent."""
    async with factory() as session:
        mine, total, _ = await _spend(session, user_id, utc_day(now))
    scope = exceeded_scope(
        user_spend=mine,
        user_budget=settings.user_daily_budget_usd,
        global_spend=total,
        global_budget=settings.global_daily_budget_usd,
    )
    if scope is not None:
        raise budget_exceeded(scope, now)


async def usage_summary(
    factory: Factory, settings: Settings, user_id: uuid.UUID, *, now: datetime
) -> UsageSummary:
    async with factory() as session:
        mine, total, row = await _spend(session, user_id, utc_day(now))
    return UsageSummary(
        day=utc_day(now),
        spend_usd=mine,
        budget_usd=settings.user_daily_budget_usd,
        tokens_used=(row.prompt_tokens + row.completion_tokens) if row is not None else 0,
        messages=row.requests if row is not None else 0,
        global_cap_reached=total >= settings.global_daily_budget_usd,
        resets_at=next_reset(now),
    )


async def note_global_spend(factory: Factory, settings: Settings, *, now: datetime) -> None:
    """Log one `global_spend_alert` per UTC day once the instance has spent 80 % of its cap.

    Meant to be called after a run is saved; the dedupe row means concurrent runs cannot alert
    twice. The log line is what the operator's alerting watches (memory/ai.md).
    """
    cap = settings.global_daily_budget_usd
    if cap <= 0:
        return
    day = utc_day(now)
    async with factory() as session:
        total = await usage_repo.global_cost(session, day)
    if total < cap * ALERT_FRACTION:
        return
    first = await ratelimit.record(
        factory, f"spend_alert:{day.isoformat()}", now=now, window_s=_DAY_S
    )
    if first == 1:
        _log.warning(
            "global_spend_alert",
            day=day.isoformat(),
            spend_usd=str(total),
            cap_usd=str(cap),
        )
