"""Deleting a file (TASK-020): index, queued ingestion, row and disk, against real Postgres and a
real Qdrant (the embeddings client is the offline fake)."""

import asyncio
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.repositories import files as repo
from app.jobs.handlers import JobContext, ingest_file
from app.jobs.queue import ClaimedJob
from app.llm.fake import FakeEmbeddingsClient
from app.rag.embedder import Embedder
from app.rag.index import QdrantIndex
from app.rag.retriever import Retriever
from app.services.files import FileService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.rag.qdrant_support import points_of

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]

TEXT = b"Monthly churn decreased to 3.1 percent in the third quarter after the pricing change."


class Body:
    """What `FileService.upload` reads: an async `read` over bytes."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1, /) -> bytes:
        data, self._data = self._data, b""
        return data


class Boom(Exception):
    pass


class Env:
    def __init__(
        self, factory: Factory, settings: Settings, index: QdrantIndex, db: psycopg.Connection[Any]
    ) -> None:
        self.factory, self.settings, self.index, self.db = factory, settings, index, db
        self.service = FileService(factory, settings, index=lambda: index)
        self.embedder = Embedder(FakeEmbeddingsClient(), settings.llm_embedding_model)

    async def upload(self, user: UUID, data: bytes = TEXT, name: str = "q3.txt") -> UUID:
        row = await self.service.upload(user, filename=name, content=Body(data))
        return row.id

    def ctx(self, index: QdrantIndex | None = None) -> JobContext:
        return JobContext(
            factory=self.factory,
            settings=self.settings,
            embedder=self.embedder,
            index=index or self.index,
        )

    async def ingest(self, user: UUID, file_id: UUID, index: QdrantIndex | None = None) -> None:
        job = ClaimedJob(
            id=1,
            kind="ingest_file",
            payload={"file_id": str(file_id)},
            attempts=1,
            max_attempts=3,
            user_id=user,
        )
        await ingest_file(self.ctx(index), job)

    async def found_by(self, user: UUID, query: str = "churn third quarter") -> list[UUID]:
        retrieved = await Retriever(self.embedder, self.index).search(user, query)
        return [p.file_id for p in retrieved.passages]

    def disk(self, user: UUID, file_id: UUID) -> Path:
        return self.settings.uploads_dir / f"{user}/{file_id}"

    def row_exists(self, file_id: UUID) -> bool:
        return (
            self.db.execute("SELECT 1 FROM files WHERE id = %s", (file_id,)).fetchone() is not None
        )

    def jobs(self) -> list[tuple[Any, ...]]:
        return self.db.execute(
            "SELECT status, payload ->> 'file_id' FROM jobs WHERE kind = 'ingest_file'"
        ).fetchall()


@pytest.fixture
async def env(
    session_factory: Factory,
    settings: Settings,
    qdrant_index: QdrantIndex,
    db: psycopg.Connection[Any],
) -> Env:
    await qdrant_index.ensure_collection()
    return Env(session_factory, settings, qdrant_index, db)


async def two_users(make_user: Any) -> tuple[UUID, UUID]:
    return UUID(make_user("alice@example.com")), UUID(make_user("bob@example.com"))


# ───────────── the TASK-020 criterion ─────────────


async def test_a_deleted_file_is_never_returned_by_search(env: Env, make_user: Any) -> None:
    alice, bob = await two_users(make_user)
    alices = await env.upload(alice)
    bobs = await env.upload(bob)  # the same text: bob's own copy
    await env.ingest(alice, alices)
    await env.ingest(bob, bobs)
    assert await env.found_by(alice) == [alices]  # the negative control: it IS found first

    await env.service.delete(alice, alices)

    assert await env.found_by(alice) == []
    assert await points_of(env.index, alices) == []
    assert await env.found_by(bob) == [bobs]  # someone else's identical file is untouched
    assert len(await points_of(env.index, bobs)) > 0


async def test_deleting_removes_the_row_and_the_disk_file_and_frees_the_slot(
    env: Env, make_user: Any
) -> None:
    alice, _ = await two_users(make_user)
    file_id = await env.upload(alice)
    assert env.disk(alice, file_id).exists()

    await env.service.delete(alice, file_id)

    assert not env.row_exists(file_id)
    assert not env.disk(alice, file_id).exists()
    again = await env.upload(alice)  # same bytes: the SHA-256 slot is free again
    assert again != file_id


async def test_a_file_that_never_finished_processing_can_be_deleted(
    env: Env, make_user: Any
) -> None:
    alice, _ = await two_users(make_user)
    file_id = await env.upload(alice)  # still `pending`: nothing was indexed

    await env.service.delete(alice, file_id)

    assert not env.row_exists(file_id)


# ───────────── ownership ─────────────


async def test_another_users_file_is_a_404_and_stays_intact(env: Env, make_user: Any) -> None:
    alice, bob = await two_users(make_user)
    file_id = await env.upload(alice)
    await env.ingest(alice, file_id)

    with pytest.raises(ApiError) as caught:
        await env.service.delete(bob, file_id)

    assert caught.value.status_code == 404
    assert env.row_exists(file_id)
    assert env.disk(alice, file_id).exists()
    assert await env.found_by(alice) == [file_id]


async def test_an_unknown_file_and_a_second_delete_are_404(env: Env, make_user: Any) -> None:
    alice, _ = await two_users(make_user)
    file_id = await env.upload(alice)
    await env.service.delete(alice, file_id)

    for missing in (file_id, uuid.uuid4()):
        with pytest.raises(ApiError) as caught:
            await env.service.delete(alice, missing)
        assert caught.value.status_code == 404


# ───────────── in-flight ingestion ─────────────


async def test_a_queued_ingestion_is_cancelled(env: Env, make_user: Any) -> None:
    alice, bob = await two_users(make_user)
    mine = await env.upload(alice)
    theirs = await env.upload(bob, b"Something else entirely.", "other.txt")

    await env.service.delete(alice, mine)

    assert sorted(env.jobs()) == sorted([("cancelled", str(mine)), ("queued", str(theirs))])


async def test_a_worker_that_still_runs_a_cancelled_job_does_nothing(
    env: Env, make_user: Any
) -> None:
    alice, _ = await two_users(make_user)
    file_id = await env.upload(alice)
    await env.service.delete(alice, file_id)

    await env.ingest(alice, file_id)  # a worker that had already claimed it

    assert await points_of(env.index, file_id) == []


class GatedIndex(QdrantIndex):
    """Lets a test order the worker's upsert and the service's delete exactly."""

    def __init__(self, base: QdrantIndex) -> None:
        super().__init__(base.client, alias=base.alias)
        self.deleted = asyncio.Event()
        self.upserted = asyncio.Event()

    async def delete_file(self, user_id: UUID, file_id: UUID) -> None:
        await super().delete_file(user_id, file_id)
        self.deleted.set()
        await asyncio.wait_for(self.upserted.wait(), 10)  # hold the delete open (row locked)

    async def upsert(self, **kw: Any) -> None:
        await asyncio.wait_for(self.deleted.wait(), 10)  # write only after the delete cleared
        await super().upsert(**kw)
        self.upserted.set()


async def test_a_delete_during_processing_leaves_no_points(env: Env, make_user: Any) -> None:
    """The bad order: our vectors are removed, THEN the worker writes its chunks, then we commit.
    The worker's `mark_ready` waits for our row lock, finds the row gone and cleans up."""
    alice, _ = await two_users(make_user)
    file_id = await env.upload(alice)
    gated = GatedIndex(env.index)
    service = FileService(env.factory, env.settings, index=lambda: gated)

    worker = asyncio.create_task(env.ingest(alice, file_id, index=gated))
    await asyncio.wait_for(asyncio.gather(worker, service.delete(alice, file_id)), 30)

    assert not env.row_exists(file_id)
    assert await points_of(env.index, file_id) == []
    assert await env.found_by(alice) == []


# ───────────── failures ─────────────


class DownIndex(QdrantIndex):
    async def delete_file(self, user_id: UUID, file_id: UUID) -> None:
        raise Boom("qdrant is down")


async def test_when_the_index_fails_nothing_is_deleted_and_a_retry_works(
    env: Env, make_user: Any
) -> None:
    alice, _ = await two_users(make_user)
    file_id = await env.upload(alice)
    await env.ingest(alice, file_id)
    broken = FileService(env.factory, env.settings, index=lambda: DownIndex(env.index.client))

    with pytest.raises(Boom):
        await broken.delete(alice, file_id)

    assert env.row_exists(file_id)
    assert env.disk(alice, file_id).exists()
    assert await env.found_by(alice) == [file_id]  # still there, consistently
    await env.service.delete(alice, file_id)  # the retry succeeds
    assert not env.row_exists(file_id)
    assert await env.found_by(alice) == []


async def test_a_disk_failure_after_the_commit_does_not_fail_the_request(
    env: Env, make_user: Any
) -> None:
    alice, _ = await two_users(make_user)
    file_id = await env.upload(alice)
    path = env.disk(alice, file_id)
    path.unlink()
    path.mkdir()  # `unlink` on a directory raises OSError

    await env.service.delete(alice, file_id)

    assert not env.row_exists(file_id)


async def test_the_repository_functions_filter_on_the_owner(env: Env, make_user: Any) -> None:
    """Even called directly, nothing of another user can be locked or deleted."""
    alice, bob = await two_users(make_user)
    file_id = await env.upload(alice)

    async with env.factory() as session, session.begin():
        assert await repo.get_for_update(session, bob, file_id) is None
        assert await repo.delete(session, bob, file_id) is False

    assert env.row_exists(file_id)
