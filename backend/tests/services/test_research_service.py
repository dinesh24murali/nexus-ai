"""The `deep_research` pre-check against a real Postgres (TASK-043): the worst case of a run is a
number that is checked against the user's budget and the global cap before anything runs, and a
PDF asked for is checked against the daily document limit first."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.services import documents as document_service
from app.services import research
from app.services.research import ResearchService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def spend(db: psycopg.Connection[Any], user: str, usd: str) -> None:
    db.execute(
        "INSERT INTO usage_daily (user_id, day, cost_usd) VALUES (%s, %s, %s)"
        " ON CONFLICT (user_id, day) DO UPDATE SET cost_usd = EXCLUDED.cost_usd",
        (user, datetime.now(UTC).date(), Decimal(usd)),
    )


def make_documents(db: psycopg.Connection[Any], user: str, count: int) -> None:
    for _ in range(count):
        db.execute(
            "INSERT INTO documents (user_id, title, kind, status, draft)"
            " VALUES (%s, 't', 'answer', 'ready', '{}'::jsonb)",
            (user,),
        )


@pytest.fixture
def user(make_user: Any) -> str:
    return str(make_user())


@pytest.fixture
def service(session_factory: Factory, settings: Settings) -> ResearchService:
    return ResearchService(session_factory, settings)


def test_the_worst_case_is_below_five_cents_by_construction(settings: Settings) -> None:
    assert research.worst_case_usd(settings) <= Decimal("0.05")
    assert research.worst_case_usd(settings) == Decimal("0.043")  # doc 08 section 2.5


async def test_a_fresh_user_may_start_a_run(service: ResearchService, user: str) -> None:
    uid = uuid.UUID(user)
    assert await service.check(uid, pdf=False) is None
    assert await service.check(uid, pdf=True) is None


async def test_a_run_that_could_cost_more_than_the_budget_left_does_not_start(
    service: ResearchService, db: psycopg.Connection[Any], user: str
) -> None:
    spend(db, user, "0.46")  # $0.04 left of $0.50: below the worst case of $0.043

    refusal = await service.check(uuid.UUID(user), pdf=False)

    assert refusal == document_service.user_budget_reply(
        Decimal("0.043"), Decimal("0.04"), "a research run"
    )
    assert "can't start a research run" in (refusal or "") and "$0.04 left" in (refusal or "")


async def test_a_budget_with_room_for_the_worst_case_is_fine(
    service: ResearchService, db: psycopg.Connection[Any], user: str
) -> None:
    spend(db, user, "0.45")  # $0.05 left
    assert await service.check(uuid.UUID(user), pdf=False) is None


async def test_the_global_cap_refuses_too_without_naming_anyones_spend(
    service: ResearchService, db: psycopg.Connection[Any], make_user: Any, user: str
) -> None:
    spend(db, str(make_user("big@example.com")), "4.99")  # the global cap is $5.00

    refusal = await service.check(uuid.UUID(user), pdf=False)

    assert refusal == document_service.global_budget_reply("a research run")
    assert "4.99" not in (refusal or "")


async def test_the_document_limit_only_matters_when_a_pdf_is_asked_for(
    service: ResearchService, db: psycopg.Connection[Any], user: str, settings: Settings
) -> None:
    make_documents(db, user, settings.documents_per_day)
    uid = uuid.UUID(user)

    assert await service.check(uid, pdf=False) is None  # a chat report is not a document
    assert await service.check(uid, pdf=True) == document_service.day_limit_reply(
        settings.documents_per_day
    )


async def test_yesterdays_documents_and_other_users_documents_do_not_count(
    service: ResearchService,
    db: psycopg.Connection[Any],
    make_user: Any,
    user: str,
    settings: Settings,
) -> None:
    make_documents(db, user, settings.documents_per_day)
    db.execute("UPDATE documents SET created_at = created_at - interval '1 day'")
    make_documents(db, str(make_user("other@example.com")), settings.documents_per_day)

    assert await service.check(uuid.UUID(user), pdf=True) is None
