"""`purge_user` (TASK-028): one user's data is gone from every store, and nobody else's is touched.

Real Postgres (rows, LangGraph checkpoints, the memory store), real Qdrant and the real uploads
directory; the embeddings are the offline fake."""

import uuid
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.db.models import User
from app.jobs import handlers, queue
from app.jobs.handlers import JobContext, PurgeBlockedError
from app.jobs.queue import ClaimedJob
from app.llm.fake import FakeEmbeddingsClient
from app.memory.store import open_memory_store
from app.rag.embedder import Embedder
from app.rag.index import QdrantIndex
from app.services.accounts import request_deletion
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.memory.conftest import remember
from tests.rag.qdrant_support import ingest

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


@pytest.fixture
def job_settings(settings: Settings, db_url: str) -> Settings:
    return settings.model_copy(update={"database_url": db_url})


@pytest.fixture
def embedder() -> Embedder:
    return Embedder(FakeEmbeddingsClient(), "text-embedding-3-small")


class World:
    """Two users with data in every store. `leaver` asks to be deleted; `stayer` must not notice."""

    def __init__(
        self,
        db: psycopg.Connection[Any],
        factory: Factory,
        settings: Settings,
        index: QdrantIndex,
        embedder: Embedder,
    ) -> None:
        self.db, self.factory, self.settings, self.index, self.embedder = (
            db,
            factory,
            settings,
            index,
            embedder,
        )
        self.threads: dict[str, list[str]] = {}
        self.file_ids: dict[str, uuid.UUID] = {}

    @property
    def ctx(self) -> JobContext:
        return JobContext(
            factory=self.factory, settings=self.settings, embedder=self.embedder, index=self.index
        )

    async def fill(self, user: str, label: str) -> None:
        uid = uuid.UUID(user)
        db = self.db
        self.threads[user] = []
        for n in range(2):
            row = db.execute(
                "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id",
                (user, f"{label} chat {n}"),
            ).fetchone()
            assert row
            thread = str(row[0])
            self.threads[user].append(thread)
            message = db.execute(
                "INSERT INTO messages (conversation_id, user_id, role, content)"
                " VALUES (%s, %s, 'assistant', %s) RETURNING id",
                (thread, user, f"{label} answer"),
            ).fetchone()
            assert message
            db.execute(
                "INSERT INTO feedback (message_id, user_id, score) VALUES (%s, %s, 1)",
                (message[0], user),
            )
            db.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint)"
                " VALUES (%s, 'c1', '{}')",
                (thread,),
            )
            db.execute(
                "INSERT INTO checkpoint_blobs (thread_id, channel, version, type, blob)"
                " VALUES (%s, 'ch', '1', 'json', '\\x00')",
                (thread,),
            )
            db.execute(
                "INSERT INTO checkpoint_writes"
                " (thread_id, checkpoint_id, task_id, idx, channel, blob)"
                " VALUES (%s, 'c1', 't1', 0, 'ch', '\\x00')",
                (thread,),
            )
        db.execute(
            "INSERT INTO usage_daily (user_id, day, prompt_tokens, completion_tokens, cost_usd,"
            " requests) VALUES (%s, current_date, 10, 5, 0.01, 1)",
            (user,),
        )
        db.execute(
            "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)"
            " VALUES (%s, gen_random_uuid(), %s, now() + interval '1 day')",
            (user, f"hash-{label}"),
        )
        db.execute(
            "INSERT INTO rate_limits (key, window_start, count) VALUES (%s, now(), 3)",
            (f"chat:{user}",),
        )
        db.execute(
            "INSERT INTO jobs (kind, payload, idempotency_key, user_id, run_after)"
            " VALUES ('generate_title', '{}', %s, %s, now() + interval '1 day')",
            (f"title-{label}", user),
        )
        # A file: its row, its bytes on disk and its points in Qdrant.
        file_id = uuid.uuid4()
        self.file_ids[user] = file_id
        relative = f"{user}/{file_id}"
        path = self.settings.uploads_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{label} file".encode())
        db.execute(
            "INSERT INTO files (id, user_id, filename, mime_type, size_bytes, sha256,"
            " storage_path, status) VALUES (%s, %s, 'a.txt', 'text/plain', 10, %s, %s, 'ready')",
            (file_id, user, f"sha-{label}", relative),
        )
        await ingest(self.index, uid, [f"{label} alpha", f"{label} beta"], file_id=file_id)
        # Memories in the LangGraph store.
        async with open_memory_store(self.settings.database_url, self.embedder) as store:
            await remember(store, uid, f"{label} likes tea")
            await remember(store, uid, f"{label} lives somewhere")

    # ── what is left ──

    def user_scoped_rows(self, user: str) -> dict[str, int]:
        """Rows owned by the user in every table that has a `user_id` column (found from the
        catalogue, so a table added later is covered without touching this test)."""
        tables = [
            r[0]
            for r in self.db.execute(
                "SELECT table_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND column_name = 'user_id'"
            ).fetchall()
        ]
        counts = {}
        for table in tables:
            row = self.db.execute(
                f"SELECT count(*) FROM {table} WHERE user_id = %s",  # noqa: S608 - catalogue names
                (user,),
            ).fetchone()
            assert row
            counts[table] = int(row[0])
        return {t: n for t, n in counts.items() if n}

    def checkpoint_rows(self, user: str) -> int:
        total = 0
        for table in CHECKPOINT_TABLES:
            row = self.db.execute(
                f"SELECT count(*) FROM {table} WHERE thread_id = ANY(%s)",  # noqa: S608 - fixed
                (self.threads[user],),
            ).fetchone()
            assert row
            total += int(row[0])
        return total

    def memory_items(self, user: str) -> int:
        row = self.db.execute(
            "SELECT count(*) FROM store WHERE prefix = %s", (f"memories.{user}",)
        ).fetchone()
        assert row
        return int(row[0])

    def memory_vectors(self, user: str) -> int:
        row = self.db.execute(
            "SELECT count(*) FROM store_vectors WHERE prefix = %s", (f"memories.{user}",)
        ).fetchone()
        assert row
        return int(row[0])

    async def points(self, user: str) -> int:
        file_id = self.file_ids[user]
        from tests.rag.qdrant_support import points_of

        return len(await points_of(self.index, file_id))

    def disk(self, user: str) -> bool:
        return (self.settings.uploads_dir / user).exists()

    def row_exists(self, table: str, user: str) -> bool:
        row = self.db.execute(f"SELECT count(*) FROM {table} WHERE id = %s", (user,)).fetchone()  # noqa: S608
        assert row
        return bool(row[0])


@pytest.fixture
async def world(
    db: psycopg.Connection[Any],
    session_factory: Factory,
    job_settings: Settings,
    qdrant_index: QdrantIndex,
    embedder: Embedder,
    make_user: Any,
) -> World:
    leaver = make_user(email="leaver@example.com", display_name="Leaver")
    stayer = make_user(email="stayer@example.com", display_name="Stayer")
    w = World(db, session_factory, job_settings, qdrant_index, embedder)
    await w.fill(leaver, "leaver")
    await w.fill(stayer, "stayer")
    w.leaver, w.stayer = leaver, stayer  # type: ignore[attr-defined]
    return w


async def ask_to_leave(w: World, user: str) -> None:
    async with w.factory() as session:
        found = (await session.execute(select(User).where(User.id == uuid.UUID(user)))).scalar_one()
    await request_deletion(w.factory, found, confirm_email=found.email)


async def run_purge(w: World, *, ctx: JobContext | None = None) -> ClaimedJob:
    job = await queue.claim(w.factory, "w1")
    assert job is not None and job.kind == "purge_user"
    await handlers.JOBS[job.kind].handler(ctx or w.ctx, job)
    return job


# ───────────────────────── the acceptance test ─────────────────────────


async def test_the_purge_leaves_nothing_of_the_user_in_any_store(world: World) -> None:
    w = world
    leaver, stayer = w.leaver, w.stayer  # type: ignore[attr-defined]
    # Arrange: everything exists before (guards the test against passing on an empty world).
    assert w.user_scoped_rows(leaver)
    assert (w.checkpoint_rows(leaver), w.memory_items(leaver), w.memory_vectors(leaver)) == (
        6,
        2,
        2,
    )
    assert await w.points(leaver) == 2 and w.disk(leaver)
    stayer_before = w.user_scoped_rows(stayer)

    await ask_to_leave(w, leaver)
    await run_purge(w)

    # Postgres: the user row and every row in any table that points at the user.
    assert not w.row_exists("users", leaver)
    assert w.user_scoped_rows(leaver) == {}
    # LangGraph checkpoints, the memory store (items and their vectors), Qdrant, the disk.
    assert w.checkpoint_rows(leaver) == 0
    assert w.memory_items(leaver) == 0 and w.memory_vectors(leaver) == 0
    assert await w.points(leaver) == 0
    assert not w.disk(leaver)
    # The rate-limit counter that was keyed by the user.
    row = w.db.execute(
        "SELECT count(*) FROM rate_limits WHERE key LIKE %s", (f"%{leaver}%",)
    ).fetchone()
    assert row == (0,)

    # Nobody else notices.
    assert w.row_exists("users", stayer)
    assert w.user_scoped_rows(stayer) == stayer_before
    assert w.checkpoint_rows(stayer) == 6
    assert (w.memory_items(stayer), w.memory_vectors(stayer)) == (2, 2)
    assert await w.points(stayer) == 2
    assert w.disk(stayer)


async def test_running_the_purge_again_changes_nothing_and_does_not_fail(world: World) -> None:
    w = world
    await ask_to_leave(w, w.leaver)  # type: ignore[attr-defined]
    job = await run_purge(w)

    # The queue can deliver a job twice after a crash: now `job.user_id` is NULL (SET NULL), so
    # the repeat has to find its user in the payload.
    again = ClaimedJob(
        id=job.id, kind=job.kind, payload=job.payload, attempts=2, max_attempts=8, user_id=None
    )
    await handlers.JOBS["purge_user"].handler(w.ctx, again)

    assert w.row_exists("users", w.stayer)  # type: ignore[attr-defined]
    assert w.user_scoped_rows(w.stayer) != {}  # type: ignore[attr-defined]


async def test_a_crash_half_way_is_finished_by_the_retry(world: World) -> None:
    w = world
    leaver = w.leaver  # type: ignore[attr-defined]
    await ask_to_leave(w, leaver)

    class DownIndex(QdrantIndex):
        async def delete_user(self, user_id: uuid.UUID) -> None:
            raise ConnectionError("qdrant is down")

    broken = JobContext(
        factory=w.factory,
        settings=w.settings,
        embedder=w.embedder,
        index=DownIndex(w.index.client, alias=w.index.alias),
    )
    job = await queue.claim(w.factory, "w1")
    assert job is not None
    with pytest.raises(ConnectionError):
        await handlers.JOBS[job.kind].handler(broken, job)
    # Nothing was lost or half-deleted: the user is still waiting, their rows are still there ...
    assert w.row_exists("users", leaver) and w.user_scoped_rows(leaver)
    # ... and the retry (Qdrant is back) finishes the work.
    await handlers.JOBS[job.kind].handler(w.ctx, job)
    assert not w.row_exists("users", leaver) and await w.points(leaver) == 0


async def test_the_purge_waits_for_a_running_job_of_the_user(world: World) -> None:
    w = world
    leaver = w.leaver  # type: ignore[attr-defined]
    w.db.execute(
        "INSERT INTO jobs (kind, payload, idempotency_key, user_id, status)"
        " VALUES ('ingest_file', '{}', 'busy', %s, 'running')",
        (leaver,),
    )
    await ask_to_leave(w, leaver)
    job = await queue.claim(w.factory, "w1")
    assert job is not None and job.kind == "purge_user"

    with pytest.raises(PurgeBlockedError):
        await handlers.JOBS[job.kind].handler(w.ctx, job)

    assert w.row_exists("users", leaver) and await w.points(leaver) == 2  # nothing was touched


async def test_an_active_user_is_never_purged(world: World) -> None:
    """A stray or replayed job must not erase someone who never asked."""
    w = world
    stayer = w.stayer  # type: ignore[attr-defined]
    async with w.factory() as session, session.begin():
        await queue.enqueue(
            session, "purge_user", {"user_id": stayer}, f"purge:{stayer}", user_id=uuid.UUID(stayer)
        )

    await run_purge(w)

    assert w.row_exists("users", stayer) and w.user_scoped_rows(stayer)
    assert await w.points(stayer) == 2 and w.memory_items(stayer) == 2


async def test_a_user_with_nothing_stored_is_purged_cleanly(
    db: psycopg.Connection[Any],
    session_factory: Factory,
    job_settings: Settings,
    qdrant_index: QdrantIndex,
    embedder: Embedder,
    make_user: Any,
) -> None:
    user = make_user()
    w = World(db, session_factory, job_settings, qdrant_index, embedder)
    await ask_to_leave(w, user)

    await run_purge(w)

    assert not w.row_exists("users", user)
    assert not w.disk(user)
