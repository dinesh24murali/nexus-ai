"""`ingest_file`: parse, chunk, embed, index and report status (TASK-018). Real Postgres and Qdrant;
the embeddings client is the offline fake (real calls belong in evals)."""

import uuid
from typing import Any
from uuid import UUID

import psycopg
import pytest
from app.core.config import Settings
from app.db.repositories import files
from app.jobs.handlers import JOBS, JobContext, ingest_file
from app.jobs.queue import ClaimedJob
from app.llm.fake import FakeEmbeddingsClient
from app.rag import formats
from app.rag.embedder import Embedded, Embedder
from app.rag.index import QdrantIndex
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.rag.builders import encrypted_pdf_bytes, pdf_bytes
from tests.rag.qdrant_support import points_of

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]

SENTENCE = "The quarterly report shows revenue growth in every region this year. "
LONG_TEXT = "\n\n".join(f"Paragraph {i}. " + SENTENCE * 12 for i in range(30)).encode()


class Boom(Exception):
    pass


class FailingEmbedder(Embedder):
    def __init__(self) -> None:
        super().__init__(FakeEmbeddingsClient(), "text-embedding-3-small")
        self.calls = 0

    async def embed(self, texts: Any) -> Embedded:
        self.calls += 1
        raise Boom("embeddings API is down")


class WriteThenFailIndex(QdrantIndex):
    """Writes the points and then fails, like a connection dropped after the batch landed."""

    async def upsert(self, **kw: Any) -> None:
        await super().upsert(**kw)
        raise Boom("qdrant went away")


class Env:
    def __init__(
        self, factory: Factory, settings: Settings, index: QdrantIndex, db: psycopg.Connection[Any]
    ) -> None:
        self.factory, self.settings, self.index, self.db = factory, settings, index, db
        self.embedder = Embedder(FakeEmbeddingsClient(), settings.llm_embedding_model)

    def ctx(
        self, *, embedder: Embedder | None = None, index: QdrantIndex | None = None
    ) -> JobContext:
        return JobContext(
            factory=self.factory,
            settings=self.settings,
            embedder=embedder or self.embedder,
            index=index or self.index,
        )

    async def add_file(
        self,
        user: UUID,
        data: bytes,
        *,
        name: str = "a.txt",
        mime: str = formats.TEXT,
        write: bool = True,
    ) -> UUID:
        file_id = uuid.uuid4()
        relative = f"{user}/{file_id}"
        if write:
            path = self.settings.uploads_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        async with self.factory() as session, session.begin():
            await files.create(
                session,
                user,
                file_id=file_id,
                filename=name,
                mime_type=mime,
                size_bytes=len(data),
                sha256=uuid.uuid4().hex + uuid.uuid4().hex,
                storage_path=relative,
            )
        return file_id

    @staticmethod
    def job(user: UUID, file_id: UUID, *, attempt: int = 1, of: int = 3) -> ClaimedJob:
        return ClaimedJob(
            id=1,
            kind="ingest_file",
            payload={"file_id": str(file_id)},
            attempts=attempt,
            max_attempts=of,
            user_id=user,
        )

    async def row(self, user: UUID, file_id: UUID) -> Any:
        async with self.factory() as session:
            return await files.get(session, user, file_id)

    def spend(self, user: UUID) -> tuple[int, Any, int] | None:
        row = self.db.execute(
            "SELECT prompt_tokens, cost_usd, requests FROM usage_daily WHERE user_id = %s", (user,)
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2])


@pytest.fixture
async def env(
    session_factory: Factory,
    settings: Settings,
    qdrant_index: QdrantIndex,
    db: psycopg.Connection[Any],
) -> Env:
    await qdrant_index.ensure_collection()
    return Env(session_factory, settings, qdrant_index, db)


# ───────────── success ─────────────


async def test_a_text_file_becomes_ready_and_searchable_by_owner(env: Env, make_user: Any) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, LONG_TEXT)

    await ingest_file(env.ctx(), env.job(user, file_id))

    row = await env.row(user, file_id)
    points = await points_of(env.index, file_id)
    assert (row.status, row.error, row.page_count) == ("ready", None, None)
    assert row.chunk_count == len(points) > 1
    assert {p.payload["user_id"] for p in points} == {str(user)}
    assert {p.payload["filename"] for p in points} == {"a.txt"}
    assert sorted(p.payload["chunk_index"] for p in points) == list(range(len(points)))


async def test_a_pdf_keeps_page_numbers_and_counts_pages(env: Env, make_user: Any) -> None:
    user = UUID(make_user())
    data = pdf_bytes([SENTENCE + "first page", SENTENCE + "second page"])
    file_id = await env.add_file(user, data, name="r.pdf", mime=formats.PDF)

    await ingest_file(env.ctx(), env.job(user, file_id))

    row = await env.row(user, file_id)
    points = await points_of(env.index, file_id)
    assert (row.status, row.page_count) == ("ready", 2)
    assert sorted(p.payload["page"] for p in points) == [1, 2]


async def test_running_the_job_twice_leaves_no_duplicates(env: Env, make_user: Any) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, LONG_TEXT)

    await ingest_file(env.ctx(), env.job(user, file_id))
    first = sorted(p.id for p in await points_of(env.index, file_id))
    await ingest_file(env.ctx(), env.job(user, file_id, attempt=2))

    row = await env.row(user, file_id)
    assert sorted(p.id for p in await points_of(env.index, file_id)) == first
    assert (row.status, row.chunk_count) == ("ready", len(first))


async def test_embedding_spend_is_recorded_as_a_background_cost(env: Env, make_user: Any) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, LONG_TEXT)

    await ingest_file(env.ctx(), env.job(user, file_id))

    spend = env.spend(user)
    assert spend is not None
    tokens, cost, requests = spend
    assert tokens > 0 and cost > 0 and requests == 0


# ───────────── permanent failures: readable reason, no retry ─────────────


async def test_a_scanned_pdf_fails_with_a_readable_reason_and_no_retry(
    env: Env, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, pdf_bytes(["", ""]), name="s.pdf", mime=formats.PDF)

    await ingest_file(env.ctx(), env.job(user, file_id))  # returns: the job is done, not retried

    row = await env.row(user, file_id)
    assert row.status == "failed"
    assert row.error == "No extractable text — scanned PDFs are not supported yet."
    assert row.chunk_count is None
    assert await points_of(env.index, file_id) == []
    assert env.spend(user) is None


async def test_a_password_protected_pdf_fails_with_that_reason(env: Env, make_user: Any) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(
        user, encrypted_pdf_bytes([SENTENCE]), name="p.pdf", mime=formats.PDF
    )

    await ingest_file(env.ctx(), env.job(user, file_id))

    assert (await env.row(user, file_id)).error == "File is password-protected."


async def test_a_file_missing_from_disk_fails_readably(env: Env, make_user: Any) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, b"x", write=False)

    await ingest_file(env.ctx(), env.job(user, file_id))

    row = await env.row(user, file_id)
    assert row.status == "failed" and "upload it again" in row.error


# ───────────── transient failures: retried, and never stuck ─────────────


async def test_a_transient_failure_is_retried_and_leaves_the_file_processing(
    env: Env, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, LONG_TEXT)
    embedder = FailingEmbedder()

    with pytest.raises(Boom):
        await ingest_file(env.ctx(embedder=embedder), env.job(user, file_id, attempt=1))

    assert (await env.row(user, file_id)).status == "processing"
    # the retry succeeds
    await ingest_file(env.ctx(), env.job(user, file_id, attempt=2))
    assert (await env.row(user, file_id)).status == "ready"


async def test_the_last_attempt_marks_the_file_failed_and_cleans_up_partial_points(
    env: Env, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, LONG_TEXT)
    index = WriteThenFailIndex(env.index.client, alias=env.index.alias)

    with pytest.raises(Boom):
        await ingest_file(env.ctx(index=index), env.job(user, file_id, attempt=3, of=3))

    row = await env.row(user, file_id)
    assert row.status == "failed"
    assert row.error == "Processing failed. Please upload the file again."
    assert await points_of(env.index, file_id) == []
    assert env.spend(user) is not None  # the embeddings were bought even though indexing failed


async def test_an_earlier_attempt_keeps_its_points_for_the_retry_to_overwrite(
    env: Env, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, LONG_TEXT)
    index = WriteThenFailIndex(env.index.client, alias=env.index.alias)

    with pytest.raises(Boom):
        await ingest_file(env.ctx(index=index), env.job(user, file_id, attempt=1))
    await ingest_file(env.ctx(), env.job(user, file_id, attempt=2))

    row = await env.row(user, file_id)
    assert row.chunk_count == len(await points_of(env.index, file_id))


# ───────────── the file or the job is not what it seems ─────────────


async def test_a_deleted_file_is_a_quiet_no_op(env: Env, make_user: Any) -> None:
    user = UUID(make_user())

    await ingest_file(env.ctx(), env.job(user, uuid.uuid4()))  # no row: nothing to do, no error

    assert env.spend(user) is None


async def test_a_file_deleted_while_it_is_processed_leaves_no_points(
    env: Env, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, LONG_TEXT)

    class DeletingEmbedder(Embedder):
        async def embed(self, texts: Any) -> Embedded:
            result = await super().embed(texts)
            env.db.execute("DELETE FROM files WHERE id = %s", (file_id,))
            return result

    embedder = DeletingEmbedder(FakeEmbeddingsClient(), "text-embedding-3-small")

    await ingest_file(env.ctx(embedder=embedder), env.job(user, file_id))

    assert await env.row(user, file_id) is None
    assert await points_of(env.index, file_id) == []


async def test_a_job_cannot_process_another_users_file(env: Env, make_user: Any) -> None:
    owner = UUID(make_user("a@example.com"))
    other = UUID(make_user("b@example.com"))
    file_id = await env.add_file(owner, LONG_TEXT)

    await ingest_file(env.ctx(), env.job(other, file_id))  # the job says it belongs to `other`

    assert (await env.row(owner, file_id)).status == "pending"
    assert await points_of(env.index, file_id) == []


async def test_a_job_without_a_user_is_refused(env: Env) -> None:
    job = ClaimedJob(1, "ingest_file", {"file_id": str(uuid.uuid4())}, 1, 3, None)

    with pytest.raises(ValueError, match="needs a user"):
        await ingest_file(env.ctx(), job)


def test_the_job_is_registered_with_a_5_minute_timeout_and_3_attempts() -> None:
    spec = JOBS["ingest_file"]

    assert (spec.timeout_s, spec.max_attempts) == (300, 3)
    assert spec.handler is ingest_file


async def test_a_failed_cleanup_does_not_hide_the_failure_from_the_user(
    env: Env, make_user: Any
) -> None:
    user = UUID(make_user())
    file_id = await env.add_file(user, pdf_bytes(["", ""]), name="s.pdf", mime=formats.PDF)

    class UnreachableIndex(QdrantIndex):
        async def delete_file(self, user_id: UUID, file_id: UUID) -> None:
            raise Boom("qdrant is down")

    index = UnreachableIndex(env.index.client, alias=env.index.alias)

    await ingest_file(env.ctx(index=index), env.job(user, file_id))

    assert (await env.row(user, file_id)).status == "failed"


def test_the_clients_are_built_once_from_settings(
    session_factory: Factory, settings: Settings
) -> None:
    ctx = JobContext(factory=session_factory, settings=settings)

    assert ctx.get_embedder() is ctx.get_embedder()
    assert ctx.get_index() is ctx.get_index()
    assert ctx.get_index().alias == settings.qdrant_collection
