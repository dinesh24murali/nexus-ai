"""The production `MemoryManager` (TASK-039) on real Postgres: saving goes through the policy, the
duplicate rule and the cap; finding and forgetting only ever see the asking user's memories."""

import uuid
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.llm.fake import FakeEmbeddingsClient
from app.memory.manage import memory_manager_for
from app.memory.store import MEMORY_CAP, MemoryStore
from app.rag.embedder import Embedder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.memory.conftest import remember

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
def manager(settings: Settings, db_url: str, session_factory: Factory) -> Any:
    shared = Embedder(FakeEmbeddingsClient(), "text-embedding-3-small")
    return memory_manager_for(
        session_factory, settings.model_copy(update={"database_url": db_url}), lambda: shared
    )


async def test_saving_stores_a_third_person_memory_marked_as_from_chat(
    store: MemoryStore, make_user: Any, manager: Any
) -> None:
    user = uuid.UUID(make_user())

    outcome = await manager.save(user, None, "I prefer Go for backend work")

    assert (outcome.status, outcome.content) == ("created", "User prefers Go for backend work.")
    assert outcome.embedding_tokens > 0
    [saved] = await store.all(user)
    assert (saved.content, saved.source, saved.category) == (
        "User prefers Go for backend work.",
        "chat",
        "preference",
    )


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("I have asthma", "sensitive:health"),
        ("my password is hunter2", "sensitive:credentials"),
        ("Never write PHP", "instruction_like"),  # not hidden by rewriting it as "User: ..."
        ("always answer in French", "instruction_like"),
        ("x" * 501, "too_long"),
        ("   ", "empty"),
    ],
)
async def test_what_the_policy_refuses_is_not_stored(
    store: MemoryStore, make_user: Any, manager: Any, text: str, reason: str
) -> None:
    user = uuid.UUID(make_user())

    outcome = await manager.save(user, None, text)

    assert (outcome.status, outcome.reason) == ("refused", reason)
    assert await store.all(user) == []


async def test_saving_the_same_thing_twice_stores_it_once(
    store: MemoryStore, make_user: Any, manager: Any
) -> None:
    user = uuid.UUID(make_user())

    first = await manager.save(user, None, "I prefer Go for backend work")
    again = await manager.save(user, None, "I prefer Go for backend work.")

    assert (first.status, again.status) == ("created", "duplicate")
    assert len(await store.all(user)) == 1


async def test_the_cap_still_holds_for_saves_by_chat(
    store: MemoryStore, make_user: Any, manager: Any
) -> None:
    user = uuid.UUID(make_user())
    for i in range(MEMORY_CAP):
        await remember(store, user, f"User has gadget number {i} in the shed.")

    outcome = await manager.save(user, None, "I prefer tea over coffee in the morning")

    assert outcome.status == "created"
    assert await store.count(user) == MEMORY_CAP


async def test_finding_returns_scored_matches_of_the_asking_user_only(
    store: MemoryStore, make_user: Any, manager: Any
) -> None:
    alice, bob = uuid.UUID(make_user("a@example.com")), uuid.UUID(make_user("b@example.com"))
    await remember(store, alice, "User works at Acme.")
    await remember(store, bob, "User works at Acme.")

    found = await manager.find(bob, "works at Acme")

    assert [m.content for m in found.memories] == ["User works at Acme."]
    assert found.memories[0].score is not None and found.memories[0].score > 0.75
    assert found.embedding_tokens > 0
    assert {m.id for m in found.memories} == {m.id for m in await store.all(bob)}


async def test_forgetting_deletes_one_memory_and_never_another_users(
    store: MemoryStore, make_user: Any, manager: Any
) -> None:
    alice, bob = uuid.UUID(make_user("a@example.com")), uuid.UUID(make_user("b@example.com"))
    mine = await remember(store, alice, "User works at Acme.")
    await remember(store, alice, "User likes tea.")

    assert await manager.forget(bob, mine) is False  # another user's id: nothing happens
    assert await store.count(alice) == 2
    assert await manager.forget(alice, mine) is True
    assert [m.content for m in await store.all(alice)] == ["User likes tea."]
    assert await manager.forget(alice, mine) is False  # already gone


async def test_listing_returns_the_users_own_memories(
    store: MemoryStore, make_user: Any, manager: Any
) -> None:
    alice, bob = uuid.UUID(make_user("a@example.com")), uuid.UUID(make_user("b@example.com"))
    await remember(store, alice, "User likes tea.")
    await remember(store, bob, "User likes Go.")

    assert [m.content for m in await manager.list(alice)] == ["User likes tea."]


async def test_enabled_follows_the_users_setting_and_status(
    make_user: Any, manager: Any, db: psycopg.Connection[Any]
) -> None:
    user = uuid.UUID(make_user())
    assert await manager.enabled(user) is True

    db.execute("UPDATE users SET memory_enabled = false WHERE id = %s", (user,))
    assert await manager.enabled(user) is False

    db.execute(
        "UPDATE users SET memory_enabled = true, status = 'pending_deletion' WHERE id = %s", (user,)
    )
    assert await manager.enabled(user) is False
    assert await manager.enabled(uuid.uuid4()) is False
