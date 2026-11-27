"""The `deep_research` pre-check (TASK-043, doc 08 section 2.5): may a run start?

A research run is bounded by construction: the plan is at most 1k tokens out on the chat model, the
report at most 17k tokens in and 4k out on the `long_form` model, so its worst case is a number
that can be computed (about $0.043) and checked before anything runs, against the user's remaining
daily budget and the global cap. A run that has started is never stopped for budget reasons. When a
PDF was asked for the daily document limit is checked as well, so the user is told up front instead
of after the report was paid for. Nothing a user wrote is logged.
"""

import uuid
from datetime import UTC, datetime, time
from decimal import ROUND_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.repositories import documents as documents_repo
from app.llm.pricing import price_for
from app.services.documents import budget_refusal, day_limit_reply

Factory = async_sessionmaker[AsyncSession]

# Doc 08 section 2.5: the plan prompt is about 1k tokens in and the plan at most 1k out (the
# `research_plan` role's max output); the report gets at most 16k tokens of results plus its prompt
# and writes at most 4k (the `long_form` role's max output, reasoning included).
PLAN_INPUT_TOKENS = 1_000
PLAN_OUTPUT_TOKENS = 1_000
REPORT_INPUT_TOKENS = 17_000
REPORT_OUTPUT_TOKENS = 4_000
WHAT = "a research run"


def worst_case_usd(settings: Settings) -> Decimal:
    """The most one research run can cost, by construction (no cache discount)."""
    plan = price_for(settings.llm_chat_model)
    report = price_for(settings.llm_long_form_model)
    cost = (
        Decimal(PLAN_INPUT_TOKENS) * plan.input
        + Decimal(PLAN_OUTPUT_TOKENS) * plan.output
        + Decimal(REPORT_INPUT_TOKENS) * report.input
        + Decimal(REPORT_OUTPUT_TOKENS) * report.output
    ) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.001"), rounding=ROUND_UP)


class ResearchService:
    def __init__(self, factory: Factory, settings: Settings) -> None:
        self._factory = factory
        self._settings = settings

    async def check(self, user_id: uuid.UUID, *, pdf: bool) -> str | None:
        """None when a research run may start; otherwise the reply that refuses it."""
        async with self._factory() as session:
            if pdf:
                today = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
                made = await documents_repo.count_since(session, user_id, today)
                if made >= self._settings.documents_per_day:
                    return day_limit_reply(self._settings.documents_per_day)
            return await budget_refusal(
                session, self._settings, user_id, worst_case_usd(self._settings), what=WHAT
            )
