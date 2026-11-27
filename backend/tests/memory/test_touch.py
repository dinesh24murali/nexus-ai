"""Marking memories as used (TASK-026), on real Postgres: the cap evicts by last use."""

import uuid
from typing import Any

import psycopg
import pytest
from app.memory.store import MemoryStore

from tests.memory.conftest import remember, users

pytestmark = pytest.mark.integration


def vectors(db: psycopg.Connection[Any]) -> int:
    row = db.execute("SELECT count(*) FROM store_vectors").fetchone()
    assert row is not None
    return int(row[0])


async def test_touch_moves_last_use_forward_and_changes_nothing_else(
    store: MemoryStore, db: psycopg.Connection[Any]
) -> None:
    (alice,) = users(1)
    memory_id = await remember(store, alice, "User prefers Go.", category="preference")
    before = await store.get(alice, memory_id)
    embedded_before = store.embedding_tokens
    rows_before = vectors(db)
    assert before is not None

    await store.touch(alice, [memory_id])

    after = await store.get(alice, memory_id)
    assert after is not None
    assert after.last_used_at > before.last_used_at
    assert (after.content, after.category, after.source) == (
        before.content,
        before.category,
        before.source,
    )
    assert after.updated_at == before.updated_at  # "last edited" is not "last used"
    assert store.embedding_tokens == embedded_before  # no new embedding
    assert vectors(db) == rows_before  # and the vector is still there
    assert (await store.search(alice, "User prefers Go.", limit=1))[0].id == memory_id


async def test_touch_does_not_bring_back_a_deleted_memory(store: MemoryStore) -> None:
    (alice,) = users(1)
    memory_id = await remember(store, alice, "User prefers Go.")
    assert await store.delete(alice, memory_id)

    await store.touch(alice, [memory_id])

    assert await store.get(alice, memory_id) is None
    assert await store.count(alice) == 0


async def test_touch_never_reaches_another_users_memory(store: MemoryStore) -> None:
    alice, bob = users()
    memory_id = await remember(store, alice, "User prefers Go.")
    before = await store.get(alice, memory_id)
    assert before is not None

    await store.touch(bob, [memory_id])  # the right key under the wrong user

    after = await store.get(alice, memory_id)
    assert after is not None and after.last_used_at == before.last_used_at


async def test_touch_with_no_ids_or_unknown_ids_is_harmless(store: MemoryStore) -> None:
    (alice,) = users(1)
    await store.touch(alice, [])
    await store.touch(alice, [uuid.uuid4()])
    assert await store.count(alice) == 0


async def test_a_memory_that_was_read_is_spared_when_the_cap_evicts(store: MemoryStore) -> None:
    (alice,) = users(1)
    oldest = await remember(store, alice, "oldest memory")
    middle = await remember(store, alice, "middle memory")
    newest = await remember(store, alice, "newest memory")

    await store.touch(alice, [oldest])  # read into a prompt: now the most recently used
    assert await store.evict_to(alice, 2) == 1

    assert await store.get(alice, middle) is None
    assert await store.get(alice, oldest) is not None
    assert await store.get(alice, newest) is not None


async def test_recent_can_be_limited_to_one_category(store: MemoryStore) -> None:
    (alice,) = users(1)
    await remember(store, alice, "User lives in Pune.", category="fact")
    pref = await remember(store, alice, "User prefers Go.", category="preference")

    recent = await store.recent(alice, limit=5, category="preference")

    assert [m.id for m in recent] == [pref]
