"""The production `DocumentService` against a real Postgres (TASK-041): the day limit, the budget
pre-check, what counts as the "last answer", and the row + render job written together."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.documents.draft import DocumentDraft, Section
from app.services import documents as service
from app.services.documents import DocumentsService, worst_case_usd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def draft(title: str = "Report") -> DocumentDraft:
    return DocumentDraft(
        title=title, sections=[Section(heading="H", body_md="Body text.")], sources=[]
    )


def conversation(db: psycopg.Connection[Any], user: str, title: str = "Planning") -> uuid.UUID:
    row = db.execute(
        "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id", (user, title)
    ).fetchone()
    assert row
    return uuid.UUID(str(row[0]))


def say(
    db: psycopg.Connection[Any],
    user: str,
    cid: uuid.UUID,
    role: str,
    text: str,
    *,
    seconds: int,
    intent: str | None = None,
    route_source: str | None = None,
    status: str = "complete",
    citations: str = "[]",
) -> None:
    db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content, intent, route_source,"
        " status, citations, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,"
        " '2026-11-22T10:00:00Z'::timestamptz + make_interval(secs => %s))",
        (cid, user, role, text, intent, route_source, status, citations, seconds),
    )


def spend(db: psycopg.Connection[Any], user: str, usd: str) -> None:
    db.execute(
        "INSERT INTO usage_daily (user_id, day, cost_usd) VALUES (%s, %s, %s)"
        " ON CONFLICT (user_id, day) DO UPDATE SET cost_usd = EXCLUDED.cost_usd",
        (user, datetime.now(UTC).date(), Decimal(usd)),
    )


def count(db: psycopg.Connection[Any], table: str, user: str | None = None) -> int:
    where = " WHERE user_id = %s" if user else ""
    row = db.execute(f"SELECT count(*) FROM {table}{where}", (user,) if user else ()).fetchone()  # noqa: S608
    assert row
    return int(row[0])


@pytest.fixture
def user(make_user: Any) -> str:
    return str(make_user())


@pytest.fixture
def docs(session_factory: Factory, settings: Settings) -> DocumentsService:
    return DocumentsService(session_factory, settings)


# ---------- the cost promise


def test_the_worst_case_is_below_five_cents_by_construction(settings: Settings) -> None:
    assert worst_case_usd(settings) <= Decimal("0.05")
    assert worst_case_usd(settings) == Decimal("0.037")  # doc 08 section 2.5


# ---------- check


async def test_a_fresh_user_may_start_any_document(docs: DocumentsService, user: str) -> None:
    uid = uuid.UUID(user)
    assert [await docs.check(uid, k) for k in ("thread_summary", "answer", "news_brief")] == [
        None,
        None,
        None,
    ]


async def test_the_day_limit_refuses_a_document_but_not_the_next_day(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str, settings: Settings
) -> None:
    uid = uuid.UUID(user)
    for _ in range(settings.documents_per_day):
        assert await docs.create(
            uid, conversation_id=None, message_id=None, kind="answer", draft=draft()
        )

    refusal = await docs.check(uid, "answer")
    assert refusal == service.day_limit_reply(settings.documents_per_day)
    # Yesterday's documents do not count.
    db.execute("UPDATE documents SET created_at = created_at - interval '1 day'")
    assert await docs.check(uid, "answer") is None


async def test_the_limit_is_per_user(
    docs: DocumentsService, make_user: Any, settings: Settings
) -> None:
    first, second = uuid.UUID(make_user("a@example.com")), uuid.UUID(make_user("b@example.com"))
    for _ in range(settings.documents_per_day):
        await docs.create(
            first, conversation_id=None, message_id=None, kind="answer", draft=draft()
        )

    assert await docs.check(first, "answer") is not None
    assert await docs.check(second, "answer") is None


async def test_a_run_that_could_cost_more_than_the_budget_left_does_not_start(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    uid = uuid.UUID(user)
    spend(db, user, "0.49")  # $0.01 left of $0.50: below the worst case of $0.037

    thread = await docs.check(uid, "thread_summary")
    brief = await docs.check(uid, "news_brief")
    answer = await docs.check(uid, "answer")  # no model call: about $0, so it may go ahead

    assert thread == service.user_budget_reply(Decimal("0.037"), Decimal("0.01"))
    assert brief == thread
    assert answer is None
    assert "$0.04" in (thread or "") and "$0.01 left" in (thread or "")


async def test_the_global_cap_refuses_too_without_naming_anyones_spend(
    docs: DocumentsService, db: psycopg.Connection[Any], make_user: Any, user: str
) -> None:
    other = str(make_user("big@example.com"))
    spend(db, other, "4.99")  # the global cap is $5.00

    refusal = await docs.check(uuid.UUID(user), "thread_summary")

    assert refusal == service.GLOBAL_BUDGET_REPLY
    assert "4.99" not in (refusal or "")


async def test_a_budget_with_room_for_the_worst_case_is_fine(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    spend(db, user, "0.40")
    assert await docs.check(uuid.UUID(user), "thread_summary") is None


# ---------- create


async def test_create_writes_the_row_and_the_render_job_together(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    uid, cid, mid = uuid.UUID(user), conversation(db, user), uuid.uuid4()

    ref = await docs.create(
        uid, conversation_id=cid, message_id=mid, kind="thread_summary", draft=draft()
    )

    assert ref is not None and ref.title == "Report" and ref.message_id == mid
    row = db.execute(
        "SELECT status, kind, conversation_id, message_id, draft->>'title'"
        " FROM documents WHERE id = %s",
        (ref.id,),
    ).fetchone()
    assert row == ("pending", "thread_summary", cid, mid, "Report")
    job = db.execute(
        "SELECT kind, idempotency_key, max_attempts, user_id, payload->>'document_id'"
        " FROM jobs WHERE kind = 'render_document'"
    ).fetchone()
    assert job == ("render_document", f"render:{ref.id}", 2, uuid.UUID(user), str(ref.id))


async def test_the_last_slot_of_the_day_goes_to_one_of_two_racing_runs(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str, settings: Settings
) -> None:
    import asyncio

    uid = uuid.UUID(user)
    for _ in range(settings.documents_per_day - 1):
        await docs.create(uid, conversation_id=None, message_id=None, kind="answer", draft=draft())

    results = await asyncio.gather(
        *(
            docs.create(uid, conversation_id=None, message_id=None, kind="answer", draft=draft())
            for _ in range(4)
        )
    )

    assert sum(r is not None for r in results) == 1
    assert count(db, "documents", user) == settings.documents_per_day


async def test_a_document_outlives_its_conversation(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    cid = conversation(db, user)
    ref = await docs.create(
        uuid.UUID(user), conversation_id=cid, message_id=None, kind="answer", draft=draft()
    )
    assert ref

    db.execute("DELETE FROM conversations WHERE id = %s", (cid,))

    row = db.execute("SELECT conversation_id FROM documents WHERE id = %s", (ref.id,)).fetchone()
    assert row == (None,)


# ---------- reading the conversation


async def test_the_last_answer_skips_everything_that_is_not_a_chat_or_research_answer(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    uid, cid = uuid.UUID(user), conversation(db, user)
    say(db, user, cid, "user", "Compare batteries", seconds=0)
    say(
        db,
        user,
        cid,
        "assistant",
        "Solid-state wins on density.",
        seconds=1,
        intent="general_chat",
        route_source="router",
    )
    say(db, user, cid, "user", "/memory", seconds=2)
    say(
        db,
        user,
        cid,
        "assistant",
        "Here is what I remember",
        seconds=3,
        intent="manage_memory",
        route_source="command",
    )
    say(db, user, cid, "user", "/help", seconds=4)
    say(
        db,
        user,
        cid,
        "assistant",
        "I can chat and more",
        seconds=5,
        intent=None,
        route_source="command",
    )
    say(db, user, cid, "user", "/pdf", seconds=6)
    say(
        db,
        user,
        cid,
        "assistant",
        "I've made **X** into a PDF",
        seconds=7,
        intent="generate_document",
        route_source="command",
    )
    say(db, user, cid, "assistant", "A failed one", seconds=8, status="failed")
    say(db, user, cid, "assistant", "", seconds=9, intent="general_chat")

    answer = await docs.last_answer(uid, cid)

    assert answer is not None and answer.text == "Solid-state wins on density."
    assert answer.title_hint == "Planning"


async def test_an_answer_from_before_routing_counts_and_the_default_title_is_no_hint(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    cid = conversation(db, user, title="New chat")
    say(db, user, cid, "assistant", "Old style answer", seconds=1)

    answer = await docs.last_answer(uuid.UUID(user), cid)

    assert answer is not None and answer.text == "Old style answer" and answer.title_hint is None


async def test_the_citations_of_the_answer_come_with_it(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    cid = conversation(db, user)
    say(
        db,
        user,
        cid,
        "assistant",
        "Fell [1]",
        seconds=1,
        intent="general_chat",
        citations='[{"index": 1, "source_type": "web", "title": "News", "url": "https://n.example/x"}]',
    )

    answer = await docs.last_answer(uuid.UUID(user), cid)

    assert answer is not None and answer.citations[0]["url"] == "https://n.example/x"


async def test_recent_messages_leave_out_the_request_itself_and_keep_order(
    docs: DocumentsService, db: psycopg.Connection[Any], user: str
) -> None:
    uid, cid = uuid.UUID(user), conversation(db, user)
    say(db, user, cid, "user", "one", seconds=0)
    say(db, user, cid, "assistant", "two", seconds=1)
    say(db, user, cid, "user", "/pdf", seconds=2)

    assert await docs.recent_messages(uid, cid, 50) == [("user", "one"), ("assistant", "two")]
    assert await docs.recent_messages(uid, cid, 1) == [("assistant", "two")]


async def test_nothing_of_another_users_conversation_is_ever_read(
    docs: DocumentsService, db: psycopg.Connection[Any], make_user: Any, user: str
) -> None:
    other = str(make_user("other@example.com"))
    cid = conversation(db, other)
    say(db, other, cid, "user", "private question", seconds=0)
    say(db, other, cid, "assistant", "private answer", seconds=1, intent="general_chat")
    say(db, other, cid, "user", "/pdf", seconds=2)

    me = uuid.UUID(user)
    assert await docs.last_answer(me, cid) is None
    assert await docs.recent_messages(me, cid, 50) == []


def test_the_day_boundary_is_utc() -> None:
    late = datetime(2026, 11, 22, 23, 59, tzinfo=UTC).astimezone(UTC)
    assert service._day_start(late) == datetime(2026, 11, 22, tzinfo=UTC)
    assert service._day_start(late + timedelta(minutes=2)) == datetime(2026, 11, 23, tzinfo=UTC)
