"""Reading Qdrant directly, so tests can check what the index really stored."""

import uuid
from dataclasses import dataclass
from typing import Any

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
