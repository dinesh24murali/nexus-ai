"""Shared helpers for the memory tests: a store on a fresh database with the offline embeddings."""

import uuid
from collections.abc import AsyncIterator

import pytest
from app.llm.fake import FakeEmbeddingsClient
from app.memory.store import MemoryStore, open_memory_store
from app.rag.embedder import Embedder


@pytest.fixture
def embedder() -> Embedder:
    return Embedder(FakeEmbeddingsClient(), "text-embedding-3-small")


@pytest.fixture
async def store(db_url: str, embedder: Embedder) -> AsyncIterator[MemoryStore]:
    async with open_memory_store(db_url, embedder) as opened:
        yield opened


async def remember(
    store: MemoryStore,
    user_id: uuid.UUID,
    content: str,
    *,
    category: str = "fact",
    source: str = "extracted",
    conversation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await store.create(
        user_id,
        content=content,
        category=category,
        source=source,
        conversation_id=conversation_id,
    )


def users(n: int = 2) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]
