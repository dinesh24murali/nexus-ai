"""The production `MemoryManager` (TASK-039): `manage_memory`'s window onto the user's memories.

Like `memory.read.memory_lookup_for`, each call opens a store on its own connection, builds the
namespace from the run's trusted user id, and reports the embedding tokens it spent so the run
bills them. The embedder is built on first use: listing and deleting need no embeddings key.

`save` applies the same rules as the extraction job and then some: the policy (deny-list and
instruction filter) is checked on the user's own words AND on the sentence that would be stored, the
0.9 duplicate rule and the 200-memory cap come from `extract.apply_plan`. Memory text is never
logged.

`StoreMemoryManager` is the store-backed part; `memory_manager_for` adds the one database check the
API needs (the user exists, is active and has memory on). The memory eval, which has no user rows,
uses the store-backed part directly.
"""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.repositories import users
from app.graph.intents.manage_memory import Found, MemoryManager, SaveOutcome
from app.memory import ops, policy
from app.memory.extract import Plan, PlannedOp, apply_plan
from app.memory.store import LazyEmbedder, Memory, MemoryStore, open_memory_store
from app.rag.embedder import Embedder

SOURCE = "chat"  # `source` of a memory saved by chat (the API's `Memory.source`)

IsEnabled = Callable[[uuid.UUID], Awaitable[bool]]


class StoreMemoryManager:
    def __init__(
        self, database_url: str, get_embedder: Callable[[], Embedder], is_enabled: IsEnabled
    ) -> None:
        self._url = database_url
        self._get_embedder = get_embedder
        self._is_enabled = is_enabled

    def _store(self) -> AbstractAsyncContextManager[MemoryStore]:
        return open_memory_store(self._url, LazyEmbedder(self._get_embedder))

    async def enabled(self, user_id: uuid.UUID) -> bool:
        return await self._is_enabled(user_id)

    async def list(self, user_id: uuid.UUID) -> list[Memory]:
        async with self._store() as store:
            return await store.all(user_id)

    async def save(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID | None, content: str
    ) -> SaveOutcome:
        said = policy.normalise(content)
        stored = ops.third_person(said)
        # Both: rewriting "Never write PHP" as "User: Never write PHP" must not hide it.
        if refusal := policy.check(said) or policy.check(stored):
            return SaveOutcome("refused", reason=refusal.reason)
        plan = Plan([PlannedOp("create", None, stored, ops.guess_category(stored))])
        async with self._store() as store:
            applied = await apply_plan(store, user_id, conversation_id, plan, source=SOURCE)
            tokens = store.embedding_tokens
        if applied.created:
            return SaveOutcome("created", content=stored, embedding_tokens=tokens)
        return SaveOutcome("duplicate", embedding_tokens=tokens)

    async def find(self, user_id: uuid.UUID, query: str) -> Found:
        async with self._store() as store:
            memories = await store.search(
                user_id, policy.normalise(query), limit=ops.FORGET_MAX_CANDIDATES
            )
            return Found(memories, embedding_tokens=store.embedding_tokens)

    async def forget(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
        async with self._store() as store:
            return await store.delete(user_id, memory_id)


def memory_manager_for(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    get_embedder: Callable[[], Embedder],
) -> MemoryManager:
    async def enabled(user_id: uuid.UUID) -> bool:
        async with factory() as session:
            user = await users.get_by_id(session, user_id)
        return user is not None and user.status == "active" and user.memory_enabled

    return StoreMemoryManager(settings.database_url, get_embedder, enabled)
