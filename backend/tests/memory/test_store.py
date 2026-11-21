"""The memory store on real Postgres with pgvector (TASK-025). Never mocked."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from app.memory.store import MEMORY_CAP, MemoryStore

from tests.memory.conftest import remember, users

pytestmark = pytest.mark.integration


async def test_a_memory_can_be_created_read_updated_and_deleted(store: MemoryStore) -> None:
    (alice,) = users(1)
    conversation = uuid.uuid4()

    memory_id = await remember(
        store, alice, "User prefers Go.", category="preference", conversation_id=conversation
    )

    created = await store.get(alice, memory_id)
    assert created is not None
    assert (created.content, created.category, created.source) == (
        "User prefers Go.",
        "preference",
        "extracted",
    )
    assert created.conversation_id == conversation

    assert await store.update(alice, memory_id, content="User prefers Rust.", category=None)
    updated = await store.get(alice, memory_id)
    assert updated is not None
    assert updated.content == "User prefers Rust."
    assert updated.category == "preference"  # kept when the update names none
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at

    assert await store.delete(alice, memory_id)
    assert await store.get(alice, memory_id) is None
    assert not await store.delete(alice, memory_id)  # already gone
    assert not await store.update(alice, memory_id, content="x", category=None)


async def test_search_finds_the_closest_memory_and_scores_are_similarities(
    store: MemoryStore,
) -> None:
    (alice,) = users(1)
    await remember(store, alice, "User prefers Go for backend work.")
    await remember(store, alice, "User lives in Pune.")

    same = await store.search(alice, "User prefers Go for backend work.", limit=2)
    related = await store.search(alice, "Which language does the user prefer for backend work?")

    assert same[0].content == "User prefers Go for backend work."
    assert same[0].score is not None and same[0].score > 0.99  # identical text = 1
    assert same[1].score is not None and same[1].score < 0.5  # unrelated text is far away
    assert related[0].content == "User prefers Go for backend work."


async def test_a_second_user_never_sees_touches_or_finds_the_first_users_memories(
    store: MemoryStore,
) -> None:
    alice, bob = users()
    secret = await remember(store, alice, "User is building a project called CANARY-7f3a.")
    await remember(store, bob, "User likes tea.")

    assert await store.get(bob, secret) is None
    assert [m.content for m in await store.search(bob, "CANARY-7f3a project")] == [
        "User likes tea."
    ]
    assert [m.content for m in await store.all(bob)] == ["User likes tea."]
    assert not await store.update(bob, secret, content="overwritten", category=None)
    assert not await store.delete(bob, secret)
    assert await store.count(bob) == 1

    # ... and nothing happened to Alice's memory.
    kept = await store.get(alice, secret)
    assert kept is not None and kept.content == "User is building a project called CANARY-7f3a."


async def test_the_least_recently_used_memories_are_evicted_over_the_cap(
    store: MemoryStore, db: psycopg.Connection[Any]
) -> None:
    alice, bob = users()
    ids = [await remember(store, alice, f"User fact number {i}.") for i in range(5)]
    other = await remember(store, bob, "User likes tea.")
    # Make memory 0 the most recently used and memory 1 the least.
    base = datetime.now(UTC)
    for rank, memory_id in enumerate([ids[1], ids[2], ids[3], ids[4], ids[0]]):
        value = (base + timedelta(minutes=rank)).isoformat()
        db.execute(
            "UPDATE store SET value = jsonb_set(value, '{last_used_at}', to_jsonb(%s::text))"
            " WHERE key = %s",
            (value, str(memory_id)),
        )

    evicted = await store.evict_to(alice, 3)

    assert evicted == 2
    left = {m.id for m in await store.all(alice)}
    assert left == {ids[3], ids[4], ids[0]}
    assert await store.get(bob, other) is not None  # another user's memories are not counted
    assert await store.evict_to(alice, 3) == 0


async def test_the_cap_is_200(store: MemoryStore) -> None:
    assert MEMORY_CAP == 200


async def test_embedding_tokens_are_counted_for_the_caller_to_bill(store: MemoryStore) -> None:
    (alice,) = users(1)
    assert store.embedding_tokens == 0

    await remember(store, alice, "User prefers Go for backend work.")
    after_write = store.embedding_tokens
    await store.search(alice, "backend language")

    assert after_write > 0
    assert store.embedding_tokens > after_write


async def test_recent_orders_by_last_use(store: MemoryStore) -> None:
    (alice,) = users(1)
    first = await remember(store, alice, "User fact one.")
    second = await remember(store, alice, "User fact two.")

    assert [m.id for m in await store.recent(alice, limit=1)] == [second]
    assert await store.update(alice, first, content="User fact one, updated.", category=None)
    assert [m.id for m in await store.recent(alice, limit=1)] == [first]
