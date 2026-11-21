"""Reading Qdrant directly, so tests can check what the index really stored."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.fake import FakeEmbeddingsClient
from app.llm.tokens import count_text
from app.rag.chunker import Chunk
from app.rag.embedder import Embedder
from app.rag.index import QdrantIndex
from qdrant_client import models


@dataclass(frozen=True)
class Described:
    vector_size: int
    distance: str
    indexed_fields: dict[str, bool]  # field -> is_tenant


@dataclass(frozen=True)
class StoredPoint:
    id: str
    payload: dict[str, Any]


async def describe(index: QdrantIndex) -> Described:
    info = await index.client.get_collection(index.alias)
    params = info.config.params.vectors
    assert isinstance(params, models.VectorParams)
    fields = {}
    for name, schema in info.payload_schema.items():
        tenant = bool(getattr(schema.params, "is_tenant", False)) if schema.params else False
        fields[name] = tenant
    return Described(params.size, params.distance.value, fields)


async def points_of(index: QdrantIndex, file_id: uuid.UUID) -> list[StoredPoint]:
    points, _ = await index.client.scroll(
        index.alias,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="file_id", match=models.MatchValue(value=str(file_id)))]
        ),
        limit=1000,
        with_payload=True,
    )
    return [StoredPoint(str(p.id), dict(p.payload or {})) for p in points]


async def drop(index: QdrantIndex) -> None:
    await index.client.delete_collection(index.collection)  # takes its alias with it


ALICE = uuid.uuid4()
BOB = uuid.uuid4()


def embedder() -> Embedder:
    return Embedder(FakeEmbeddingsClient(), "fake")


async def ingest(
    index: QdrantIndex,
    user_id: uuid.UUID,
    texts: list[str],
    *,
    filename: str = "a.pdf",
    pages: Sequence[int | None] | None = None,
    file_id: uuid.UUID | None = None,
) -> uuid.UUID:
    file_id = file_id or uuid.uuid4()
    page_of: Sequence[int | None] = pages or [1] * len(texts)
    chunks = [
        Chunk(index=i, page=page_of[i], text=t, tokens=count_text(t)) for i, t in enumerate(texts)
    ]
    vectors = (await embedder().embed(texts)).vectors
    await index.ensure_collection()
    await index.upsert(
        user_id=user_id, file_id=file_id, filename=filename, chunks=chunks, vectors=vectors
    )
    return file_id
