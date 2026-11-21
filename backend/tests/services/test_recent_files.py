"""The recent-files lookup behind the late context message (doc 08 section 6)."""

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.db.repositories import files
from app.services.files import recent_files_for
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


async def test_lists_ready_files_with_id_name_and_upload_date(
    session_factory: Factory, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await files.create(
            session,
            user,
            file_id=file_id,
            filename="q3-report.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            sha256="a" * 64,
            storage_path="p",
        )
        await files.mark_ready(session, user, file_id, chunk_count=1, page_count=1)
        await session.execute(
            text("UPDATE files SET created_at = :at WHERE id = :id"),
            {"at": datetime(2026, 11, 20, 9, 30, tzinfo=UTC), "id": file_id},
        )

    listed = await recent_files_for(session_factory)(user)

    assert listed == [
        {"id": str(file_id), "filename": "q3-report.pdf", "uploaded_at": "2026-11-20"}
    ]


async def test_a_user_without_files_gets_an_empty_list(
    session_factory: Factory, make_user: Any
) -> None:
    assert await recent_files_for(session_factory)(UUID(make_user())) == []
