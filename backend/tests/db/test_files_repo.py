"""Files table and repository against a real Postgres (TASK-017/018)."""

import uuid
from typing import Any
from uuid import UUID

import psycopg
import pytest
from app.db.repositories import files
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


async def add(factory: Factory, user: UUID, *, name: str = "a.pdf", sha: str | None = None) -> UUID:
    file_id = uuid.uuid4()
    async with factory() as session, session.begin():
        await files.create(
            session,
            user,
            file_id=file_id,
            filename=name,
            mime_type="application/pdf",
            size_bytes=10,
            sha256=sha or uuid.uuid4().hex + uuid.uuid4().hex,
            storage_path=f"{user}/{file_id}",
        )
    return file_id


async def test_a_new_file_is_pending_with_unknown_counts(
    session_factory: Factory, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await add(session_factory, user)

    async with session_factory() as session:
        row = await files.get(session, user, file_id)

    assert row is not None
    assert (row.status, row.error, row.page_count, row.chunk_count) == ("pending", None, None, None)


async def test_an_unknown_status_is_rejected_by_the_database(
    db: psycopg.Connection[Any], make_user: Any
) -> None:
    user = make_user()

    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO files (user_id, filename, mime_type, size_bytes, sha256, storage_path,"
            " status) VALUES (%s, 'a', 'text/plain', 1, 'x', 'p', 'uploaded')",
            (user,),
        )


async def test_another_users_file_is_invisible(session_factory: Factory, make_user: Any) -> None:
    owner = UUID(make_user("a@example.com"))
    other = UUID(make_user("b@example.com"))
    file_id = await add(session_factory, owner)

    async with session_factory() as session:
        assert await files.get(session, other, file_id) is None
        assert await files.count(session, other) == 0
        assert await files.list_page(session, other, limit=10, cursor=None) == ([], None)
    async with session_factory() as session, session.begin():
        assert not await files.mark_processing(session, other, file_id)


async def test_the_same_content_twice_for_one_user_is_a_constraint_error(
    session_factory: Factory, make_user: Any
) -> None:
    user = UUID(make_user())
    await add(session_factory, user, sha="a" * 64)

    with pytest.raises(IntegrityError):
        await add(session_factory, user, sha="a" * 64)


async def test_the_same_content_for_two_users_is_fine(
    session_factory: Factory, make_user: Any
) -> None:
    first, second = UUID(make_user("a@example.com")), UUID(make_user("b@example.com"))

    await add(session_factory, first, sha="b" * 64)
    await add(session_factory, second, sha="b" * 64)

    async with session_factory() as session:
        found = await files.get_by_sha(session, second, "b" * 64)
    assert found is not None and found.user_id == second


async def test_listing_is_newest_first_and_pages_without_gaps(
    session_factory: Factory, make_user: Any
) -> None:
    user = UUID(make_user())
    ids = [await add(session_factory, user, name=f"{i}.pdf") for i in range(5)]

    async with session_factory() as session:
        first, cursor = await files.list_page(session, user, limit=2, cursor=None)
        assert cursor is not None
        second, cursor2 = await files.list_page(
            session, user, limit=2, cursor=files.decode_cursor(cursor)
        )
        third, cursor3 = await files.list_page(
            session, user, limit=2, cursor=files.decode_cursor(cursor2 or "")
        )

    assert [f.id for f in first + second + third] == list(reversed(ids))
    assert cursor3 is None


async def test_status_moves_pending_to_processing_to_ready(
    session_factory: Factory, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await add(session_factory, user)

    async with session_factory() as session, session.begin():
        assert await files.mark_processing(session, user, file_id)
    async with session_factory() as session, session.begin():
        assert await files.mark_ready(session, user, file_id, chunk_count=7, page_count=3)
    async with session_factory() as session:
        row = await files.get(session, user, file_id)

    assert row is not None
    assert (row.status, row.chunk_count, row.page_count, row.error) == ("ready", 7, 3, None)


async def test_a_failure_keeps_its_reason_and_clears_nothing_else(
    session_factory: Factory, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await add(session_factory, user)

    async with session_factory() as session, session.begin():
        assert await files.mark_failed(session, user, file_id, "File is password-protected.")
    async with session_factory() as session:
        row = await files.get(session, user, file_id)

    assert row is not None
    assert (row.status, row.error) == ("failed", "File is password-protected.")


async def test_marking_a_deleted_file_reports_it_is_gone(
    session_factory: Factory, make_user: Any
) -> None:
    user = UUID(make_user())

    async with session_factory() as session, session.begin():
        assert not await files.mark_ready(
            session, user, uuid.uuid4(), chunk_count=1, page_count=None
        )


async def test_ready_clears_an_earlier_error(session_factory: Factory, make_user: Any) -> None:
    user = UUID(make_user())
    file_id = await add(session_factory, user)
    async with session_factory() as session, session.begin():
        await files.mark_failed(session, user, file_id, "x")
    async with session_factory() as session, session.begin():
        await files.mark_ready(session, user, file_id, chunk_count=1, page_count=None)
    async with session_factory() as session:
        row = await files.get(session, user, file_id)

    assert row is not None and row.error is None
