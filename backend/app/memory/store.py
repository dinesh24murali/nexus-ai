"""The per-user memory store (docs/02_outputs/08 section 7).

`AsyncPostgresStore` with the namespace `("memories", user_id)` and an embedding index over the
`content` field. Every method takes the user id and builds the namespace itself, so there is no way
to ask for another user's memories by passing a different namespace or key: a key that exists only
under another user's namespace is simply not found.

Values: `{content, category, source, conversation_id, created_at, updated_at, last_used_at}`. Text
is untrusted user data: nothing here logs it.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.store.postgres.base import PostgresIndexConfig

from app.rag.embedder import Embedder

EMBEDDING_DIMS = 1536  # text-embedding-3-small; the same size as the document index
MEMORY_CAP = 200  # per user; the least recently used is evicted (doc 08 section 7)
MAX_CONTENT_CHARS = 500  # the API's limit (MemoryInput)
NAMESPACE_ROOT = "memories"

CATEGORIES = ("preference", "fact", "goal", "context")
SOURCES = ("extracted", "chat", "manual")


def namespace(user_id: uuid.UUID) -> tuple[str, str]:
    return (NAMESPACE_ROOT, str(user_id))


def libpq(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@dataclass(frozen=True)
class Memory:
    id: uuid.UUID
    content: str
    category: str
    source: str
    conversation_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime
    score: float | None = None  # similarity to the query, 1 = identical (search results only)


def _memory(item: Any) -> Memory:
    value = item.value
    conversation = value.get("conversation_id")
    return Memory(
        id=uuid.UUID(item.key),
        content=str(value["content"]),
        category=str(value.get("category", "fact")),
        source=str(value.get("source", "extracted")),
        conversation_id=uuid.UUID(conversation) if conversation else None,
        created_at=item.created_at,
        updated_at=item.updated_at,
        last_used_at=datetime.fromisoformat(
            str(value.get("last_used_at", item.updated_at.isoformat()))
        ),
        score=getattr(item, "score", None),
    )


def index_config(embed: Any) -> PostgresIndexConfig:
    return {
        "dims": EMBEDDING_DIMS,
        "embed": embed,
        "fields": ["content"],
        "distance_type": "cosine",
    }


class MemoryStore:
    """Memories of any number of users; each call names the user it acts for."""

    def __init__(self, store: AsyncPostgresStore, embedder: CountingEmbedder) -> None:
        self._store = store
        self._embedder = embedder

    @property
    def embedding_tokens(self) -> int:
        """Embedding tokens this store has used so far (billed to the user by the caller)."""
        return self._embedder.tokens

    async def search(self, user_id: uuid.UUID, query: str, *, limit: int = 20) -> list[Memory]:
        """The memories closest to `query`, most similar first."""
        items = await self._store.asearch(namespace(user_id), query=query, limit=limit)
        return [_memory(i) for i in items]

    async def recent(self, user_id: uuid.UUID, *, limit: int = 5) -> list[Memory]:
        """Newest first, by last use."""
        everything = await self.all(user_id)
        return sorted(everything, key=lambda m: m.last_used_at, reverse=True)[:limit]

    async def all(self, user_id: uuid.UUID) -> list[Memory]:
        found: list[Memory] = []
        while True:
            page = await self._store.asearch(namespace(user_id), limit=100, offset=len(found))
            found.extend(_memory(i) for i in page)
            if len(page) < 100:
                return found

    async def get(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> Memory | None:
        item = await self._store.aget(namespace(user_id), str(memory_id))
        return _memory(item) if item else None

    async def count(self, user_id: uuid.UUID) -> int:
        return len(await self.all(user_id))

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        content: str,
        category: str,
        source: str,
        conversation_id: uuid.UUID | None,
    ) -> uuid.UUID:
        now = datetime.now(UTC).isoformat()
        memory_id = uuid.uuid4()
        await self._store.aput(
            namespace(user_id),
            str(memory_id),
            {
                "content": content,
                "category": category,
                "source": source,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "created_at": now,
                "updated_at": now,
                "last_used_at": now,
            },
        )
        return memory_id

    async def update(
        self, user_id: uuid.UUID, memory_id: uuid.UUID, *, content: str, category: str | None
    ) -> bool:
        """Replace the text (and, if given, the category). False if there is no such memory."""
        current = await self._store.aget(namespace(user_id), str(memory_id))
        if current is None:
            return False
        now = datetime.now(UTC).isoformat()
        value = {
            **current.value,
            "content": content,
            "category": category or current.value.get("category", "fact"),
            "updated_at": now,
            "last_used_at": now,
        }
        await self._store.aput(namespace(user_id), str(memory_id), value)
        return True

    async def delete(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
        existed = await self._store.aget(namespace(user_id), str(memory_id)) is not None
        if existed:
            await self._store.adelete(namespace(user_id), str(memory_id))
        return existed

    async def evict_to(self, user_id: uuid.UUID, cap: int = MEMORY_CAP) -> int:
        """Delete the least recently used memories until at most `cap` are left."""
        everything = await self.all(user_id)
        surplus = len(everything) - cap
        if surplus <= 0:
            return 0
        oldest = sorted(everything, key=lambda m: (m.last_used_at, m.created_at))[:surplus]
        for memory in oldest:
            await self._store.adelete(namespace(user_id), str(memory.id))
        return len(oldest)


class CountingEmbedder:
    """The store's embedding function: batches go to the embeddings client, tokens are counted."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self.tokens = 0

    async def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        embedded = await self._embedder.embed(list(texts))
        self.tokens += embedded.tokens
        return embedded.vectors


@asynccontextmanager
async def open_memory_store(database_url: str, embedder: Embedder) -> AsyncIterator[MemoryStore]:
    """A store on its own connection, with its own token counter (one per job)."""
    counting = CountingEmbedder(embedder)
    async with AsyncPostgresStore.from_conn_string(
        libpq(database_url), index=index_config(counting)
    ) as store:
        yield MemoryStore(store, counting)
