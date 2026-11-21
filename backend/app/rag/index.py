"""The Qdrant side of ingestion (ADR-0003, doc 08 section 6).

One collection per index version (`documents_v1`) behind an alias (`documents`), so a re-index can
build `documents_v2` and switch the alias in one atomic step. Every point carries the owner's
`user_id` (a tenant-indexed payload field); every read or delete filters on it. Point ids are
`uuid5(file_id, chunk_index)`, so ingesting a file twice overwrites instead of duplicating.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.rag.chunker import Chunk

VECTOR_SIZE = 1536  # text-embedding-3-small; changing the model means a new collection version
UPSERT_BATCH = 100
_VERSION = "v1"


def point_id(file_id: uuid.UUID, chunk_index: int) -> uuid.UUID:
    return uuid.uuid5(file_id, str(chunk_index))


def _eq(key: str, value: uuid.UUID) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))


@dataclass(frozen=True)
class Hit:
    file_id: uuid.UUID
    filename: str
    page: int | None
    chunk_index: int
    text: str
    score: float


class QdrantIndex:
    def __init__(self, client: AsyncQdrantClient, *, alias: str = "documents") -> None:
        self.client = client
        self.alias = alias
        self.collection = f"{alias}_{_VERSION}"

    async def ensure_collection(self) -> None:
        """Create the collection, its payload indexes and the alias if missing. Idempotent."""
        aliases = (await self.client.get_aliases()).aliases
        if any(a.alias_name == self.alias for a in aliases):
            return
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(
                    size=VECTOR_SIZE, distance=models.Distance.COSINE
                ),
            )
            await self.client.create_payload_index(
                self.collection,
                "user_id",
                field_schema=models.KeywordIndexParams(
                    type=models.KeywordIndexType.KEYWORD, is_tenant=True
                ),
            )
            await self.client.create_payload_index(
                self.collection, "file_id", field_schema=models.PayloadSchemaType.KEYWORD
            )
        await self.client.update_collection_aliases(
            change_aliases_operations=[
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=self.collection, alias_name=self.alias
                    )
                )
            ]
        )

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        file_id: uuid.UUID,
        filename: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        """Write all chunks of a file, then drop points left over from a longer earlier run."""
        if len(chunks) != len(vectors):
            raise ValueError("need exactly one vector per chunk")
        for start in range(0, len(chunks), UPSERT_BATCH):
            batch = [
                models.PointStruct(
                    id=str(point_id(file_id, chunk.index)),
                    vector=list(vector),
                    payload={
                        "user_id": str(user_id),
                        "file_id": str(file_id),
                        "filename": filename,
                        "page": chunk.page,
                        "chunk_index": chunk.index,
                        "text": chunk.text,
                    },
                )
                for chunk, vector in zip(
                    chunks[start : start + UPSERT_BATCH],
                    vectors[start : start + UPSERT_BATCH],
                    strict=True,
                )
            ]
            await self.client.upsert(self.alias, points=batch, wait=True)
        await self.client.delete(
            self.alias,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        _eq("user_id", user_id),
                        _eq("file_id", file_id),
                        models.FieldCondition(
                            key="chunk_index", range=models.Range(gte=len(chunks))
                        ),
                    ]
                )
            ),
            wait=True,
        )

    async def delete_file(self, user_id: uuid.UUID, file_id: uuid.UUID) -> None:
        await self.client.delete(
            self.alias,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[_eq("user_id", user_id), _eq("file_id", file_id)])
            ),
            wait=True,
        )

    async def search(
        self,
        *,
        user_id: uuid.UUID,
        vector: Sequence[float],
        file_ids: Sequence[uuid.UUID] | None = None,
        limit: int,
        score_threshold: float,
    ) -> list[Hit]:
        """Nearest chunks of ONE user's files. The `user_id` condition is mandatory and added
        here, so no caller can search without it (LLM-T05); `file_ids` only narrows further."""
        must: list[models.Condition] = [_eq("user_id", user_id)]
        if file_ids:
            must.append(
                models.FieldCondition(
                    key="file_id", match=models.MatchAny(any=[str(f) for f in file_ids])
                )
            )
        response = await self.client.query_points(
            self.alias,
            query=list(vector),
            query_filter=models.Filter(must=must),
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
        )
        return [
            Hit(
                file_id=uuid.UUID(str(point.payload["file_id"])),
                filename=str(point.payload["filename"]),
                page=point.payload["page"],
                chunk_index=int(point.payload["chunk_index"]),
                text=str(point.payload["text"]),
                score=float(point.score),
            )
            for point in response.points
            if point.payload
        ]
