"""The production memory lookup (TASK-026) on real Postgres: relevance, the user's setting, and
above all whose memories can be read."""

import uuid
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.llm.fake import FakeEmbeddingsClient
from app.memory.read import memory_lookup_for
from app.memory.store import MemoryStore
from app.rag.embedder import Embedder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.memory.conftest import remember

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
def lookup_for(settings: Settings, db_url: str, session_factory: Factory) -> Any:
    def make(embedder: Embedder | None = None) -> Any:
        shared = embedder or Embedder(FakeEmbeddingsClient(), "text-embedding-3-small")
        return memory_lookup_for(
            session_factory, settings.model_copy(update={"database_url": db_url}), lambda: shared
        )

    return make


async def test_the_memories_closest_to_the_message_are_returned(
    store: MemoryStore, make_user: Any, lookup_for: Any
) -> None:
    user = uuid.UUID(make_user())
    await remember(store, user, "User prefers Go for backend work.")
    await remember(store, user, "User lives in Pune.")

    loaded = await lookup_for()(user, "Which language should I use for backend work?")

    assert loaded.texts[0] == "User prefers Go for backend work."
    assert set(loaded.texts) == {"User prefers Go for backend work.", "User lives in Pune."}
    assert loaded.embedding_tokens > 0  # the query was embedded and is billed to the run


async def test_recent_preferences_are_added_even_when_they_do_not_match(
    store: MemoryStore, make_user: Any, lookup_for: Any
) -> None:
    user = uuid.UUID(make_user())
    for i in range(10):
        await remember(store, user, f"Relevant fact number {i} about kubernetes.")
    await remember(store, user, "User likes dark mode.", category="preference")

    loaded = await lookup_for()(user, "kubernetes fact")

    assert "User likes dark mode." in loaded.texts
    assert len(loaded.texts) == 9  # 8 closest + the preference


async def test_user_b_never_sees_user_as_memories(
    store: MemoryStore, make_user: Any, lookup_for: Any
) -> None:
    alice = uuid.UUID(make_user(email="alice@example.com"))
    bob = uuid.UUID(make_user(email="bob@example.com"))
    await remember(store, alice, "Alice's secret: CANARY-7f3a prefers Go.", category="preference")
    await remember(store, bob, "Bob prefers Rust.", category="preference")

    # Bob asks with Alice's exact text, the strongest possible match.
    loaded = await lookup_for()(bob, "Alice's secret: CANARY-7f3a prefers Go.")

    assert loaded.texts == ["Bob prefers Rust."]
    assert not any("CANARY" in t for t in loaded.texts)


async def test_with_memory_off_nothing_is_read_and_nothing_is_embedded(
    store: MemoryStore, make_user: Any, db: psycopg.Connection[Any], lookup_for: Any
) -> None:
    user = uuid.UUID(make_user())
    await remember(store, user, "User prefers Go.", category="preference")
    db.execute("UPDATE users SET memory_enabled = false WHERE id = %s", (user,))
    counting = Embedder(FakeEmbeddingsClient(), "text-embedding-3-small")

    loaded = await lookup_for(counting)(user, "Which language?")

    assert loaded.texts == []
    assert loaded.embedding_tokens == 0


async def test_an_unknown_user_gets_nothing(lookup_for: Any) -> None:
    loaded = await lookup_for()(uuid.uuid4(), "anything")
    assert loaded.texts == []


async def test_a_deleted_memory_is_no_longer_read(
    store: MemoryStore, make_user: Any, lookup_for: Any
) -> None:
    user = uuid.UUID(make_user())
    memory_id = await remember(store, user, "User prefers Go.", category="preference")
    assert (await lookup_for()(user, "Go")).texts == ["User prefers Go."]

    await store.delete(user, memory_id)

    assert (await lookup_for()(user, "Go")).texts == []


async def test_reading_marks_the_memories_as_used(
    store: MemoryStore, make_user: Any, lookup_for: Any
) -> None:
    user = uuid.UUID(make_user())
    read = await remember(store, user, "User prefers Go.", category="preference")
    unread = await remember(store, user, "Unrelated pottery hobby.")
    before = (await store.get(user, read), await store.get(user, unread))
    assert before[0] is not None and before[1] is not None

    await lookup_for()(user, "User prefers Go.")  # shows the Go memory (and the pottery one)
    after_read = await store.get(user, read)

    assert after_read is not None and after_read.last_used_at > before[0].last_used_at


async def test_an_empty_message_still_gets_recent_preferences_without_embedding(
    store: MemoryStore, make_user: Any, lookup_for: Any
) -> None:
    user = uuid.UUID(make_user())
    await remember(store, user, "User prefers Go.", category="preference")

    loaded = await lookup_for()(user, "   ")

    assert loaded.texts == ["User prefers Go."]
    assert loaded.embedding_tokens == 0


async def test_a_user_without_memories_costs_no_embedding(make_user: Any, lookup_for: Any) -> None:
    user = uuid.UUID(make_user())

    loaded = await lookup_for()(user, "Which language should I use?")

    assert loaded.texts == []
    assert loaded.embedding_tokens == 0
