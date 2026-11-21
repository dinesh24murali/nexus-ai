"""Run lease and message read model queries against a real Postgres."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from app.db.repositories import conversations, messages
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def conv(db: psycopg.Connection[Any], user: str) -> UUID:
    row = db.execute(
        "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (user,)
    ).fetchone()
    assert row
    return UUID(str(row[0]))


async def test_only_one_run_can_hold_the_lease(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = UUID(make_user())
    cid = conv(db, str(user))

    async def acquire() -> bool:
        async with session_factory() as session, session.begin():
            return await conversations.acquire_lease(session, user, cid, uuid4())

    results = await asyncio.gather(*(acquire() for _ in range(8)))

    assert results.count(True) == 1


async def test_the_lease_is_owner_scoped(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    owner, other = make_user("a@example.com"), make_user("b@example.com")
    cid = conv(db, owner)

    async with session_factory() as session, session.begin():
        assert await conversations.acquire_lease(session, UUID(other), cid, uuid4()) is False
        assert await conversations.acquire_lease(session, UUID(owner), uuid4(), uuid4()) is False


async def test_acquiring_sets_the_heartbeat_and_release_clears_it(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = UUID(make_user())
    cid = conv(db, str(user))
    run = uuid4()

    async with session_factory() as session, session.begin():
        assert await conversations.acquire_lease(session, user, cid, run)
    row = db.execute(
        "SELECT active_run_id, run_heartbeat_at FROM conversations WHERE id = %s", (cid,)
    ).fetchone()
    assert row and row[0] == run and row[1] is not None

    async with session_factory() as session, session.begin():
        assert await conversations.release_lease(session, cid, run) is True
    row = db.execute(
        "SELECT active_run_id, run_heartbeat_at FROM conversations WHERE id = %s", (cid,)
    ).fetchone()
    assert row == (None, None)


async def test_release_and_heartbeat_fail_for_a_run_that_lost_the_lease(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = UUID(make_user())
    cid = conv(db, str(user))
    mine, theirs = uuid4(), uuid4()
    async with session_factory() as session, session.begin():
        await conversations.acquire_lease(session, user, cid, theirs)

    async with session_factory() as session, session.begin():
        assert await conversations.heartbeat_lease(session, cid, mine) is False
        assert await conversations.release_lease(session, cid, mine) is False
    row = db.execute("SELECT active_run_id FROM conversations WHERE id = %s", (cid,)).fetchone()
    assert row and row[0] == theirs  # untouched


async def test_a_heartbeat_moves_the_timestamp_forward(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = UUID(make_user())
    cid = conv(db, str(user))
    run = uuid4()
    async with session_factory() as session, session.begin():
        await conversations.acquire_lease(session, user, cid, run)
    db.execute("UPDATE conversations SET run_heartbeat_at = now() - interval '50 seconds'")

    async with session_factory() as session, session.begin():
        assert await conversations.heartbeat_lease(session, cid, run) is True

    row = db.execute("SELECT run_heartbeat_at FROM conversations WHERE id = %s", (cid,)).fetchone()
    assert row and row[0] > datetime.now(UTC) - timedelta(seconds=5)


def add_message(
    db: psycopg.Connection[Any], cid: UUID, user: str, text: str, seconds: int, role: str = "user"
) -> UUID:
    row = db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content, created_at)"
        " VALUES (%s, %s, %s, %s, '2026-11-01T12:00:00Z'::timestamptz + make_interval(secs => %s))"
        " RETURNING id",
        (cid, user, role, text, seconds),
    ).fetchone()
    assert row
    return UUID(str(row[0]))


async def test_messages_page_newest_first_with_a_stable_cursor(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = make_user()
    cid = conv(db, user)
    for i in range(5):
        add_message(db, cid, user, f"m{i}", i)

    async with session_factory() as session:
        page1, cursor = await messages.list_page(session, UUID(user), cid, limit=2, cursor=None)
        assert [m.content for m in page1] == ["m4", "m3"]
        assert cursor is not None
        add_message(db, cid, user, "m-new", 100)  # arrives between requests
        page2, cursor2 = await messages.list_page(
            session, UUID(user), cid, limit=2, cursor=conversations.decode_cursor(cursor)
        )
        page3, cursor3 = await messages.list_page(
            session, UUID(user), cid, limit=2, cursor=conversations.decode_cursor(cursor2 or "")
        )

    assert [m.content for m in page2] == ["m2", "m1"]  # no duplicate, no skip
    assert [m.content for m in page3] == ["m0"]
    assert cursor3 is None


async def test_messages_of_another_users_conversation_are_not_listed(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    owner, other = make_user("a@example.com"), make_user("b@example.com")
    cid = conv(db, owner)
    add_message(db, cid, owner, "secret", 0)

    async with session_factory() as session:
        page, _ = await messages.list_page(session, UUID(other), cid, limit=10, cursor=None)

    assert page == []


async def test_latest_and_delete(
    session_factory: Factory, db: psycopg.Connection[Any], make_user: Any
) -> None:
    owner, other = make_user("a@example.com"), make_user("b@example.com")
    cid = conv(db, owner)
    add_message(db, cid, owner, "q", 0)
    answer = add_message(db, cid, owner, "a", 1, role="assistant")

    async with session_factory() as session, session.begin():
        latest = await messages.latest(session, UUID(owner), cid)
        assert latest and latest.id == answer
        assert await messages.latest(session, UUID(other), cid) is None
        assert await messages.delete_message(session, UUID(other), answer) is False  # not theirs
        assert await messages.delete_message(session, UUID(owner), answer) is True

    row = db.execute("SELECT count(*) FROM messages").fetchone()
    assert row and row[0] == 1
