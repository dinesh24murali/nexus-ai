"""The Qdrant index against a real Qdrant (ADR-0003). Needs `docker compose up -d qdrant`."""

import uuid

import pytest
from app.core.config import Settings
from app.rag.chunker import Chunk
from app.rag.index import QdrantIndex, point_id

from tests.conftest import QDRANT_URL

from .qdrant_support import describe, points_of

pytestmark = pytest.mark.integration

DIM = 1536


def vec(hot: int) -> list[float]:
    v = [0.0] * DIM
    v[hot] = 1.0
    return v


def chunks(n: int) -> list[Chunk]:
    return [Chunk(index=i, page=i + 1, text=f"chunk {i}", tokens=3) for i in range(n)]


async def upsert(
    index: QdrantIndex, user_id: uuid.UUID, file_id: uuid.UUID, n: int, name: str = "a.pdf"
) -> None:
    await index.upsert(
        user_id=user_id,
        file_id=file_id,
        filename=name,
        chunks=chunks(n),
        vectors=[vec(i) for i in range(n)],
    )


async def test_ensure_collection_is_idempotent_and_sets_tenant_index(
    qdrant_index: QdrantIndex,
) -> None:
    await qdrant_index.ensure_collection()
    await qdrant_index.ensure_collection()

    info = await describe(qdrant_index)
    assert info.vector_size == DIM
    assert info.distance == "Cosine"
    assert info.indexed_fields == {"user_id": True, "file_id": False}  # field -> is_tenant


async def test_points_carry_the_tenant_payload_and_a_deterministic_id(
    qdrant_index: QdrantIndex,
) -> None:
    await qdrant_index.ensure_collection()
    user, file = uuid.uuid4(), uuid.uuid4()

    await upsert(qdrant_index, user, file, 3, name="report.pdf")

    points = await points_of(qdrant_index, file)
    assert sorted(p.id for p in points) == sorted(str(point_id(file, i)) for i in range(3))
    first = next(p for p in points if p.payload["chunk_index"] == 0)
    assert first.payload == {
        "user_id": str(user),
        "file_id": str(file),
        "filename": "report.pdf",
        "page": 1,
        "chunk_index": 0,
        "text": "chunk 0",
    }


async def test_running_the_same_upsert_twice_leaves_no_duplicates(
    qdrant_index: QdrantIndex,
) -> None:
    await qdrant_index.ensure_collection()
    user, file = uuid.uuid4(), uuid.uuid4()

    await upsert(qdrant_index, user, file, 4)
    await upsert(qdrant_index, user, file, 4)

    assert len(await points_of(qdrant_index, file)) == 4


async def test_a_shorter_re_ingestion_removes_the_stale_tail(qdrant_index: QdrantIndex) -> None:
    await qdrant_index.ensure_collection()
    user, file = uuid.uuid4(), uuid.uuid4()
    await upsert(qdrant_index, user, file, 5)

    await upsert(qdrant_index, user, file, 2)

    assert sorted(p.payload["chunk_index"] for p in await points_of(qdrant_index, file)) == [0, 1]


async def test_delete_file_is_scoped_to_the_owner(qdrant_index: QdrantIndex) -> None:
    await qdrant_index.ensure_collection()
    owner, other, file = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await upsert(qdrant_index, owner, file, 3)

    await qdrant_index.delete_file(other, file)
    assert len(await points_of(qdrant_index, file)) == 3

    await qdrant_index.delete_file(owner, file)
    assert await points_of(qdrant_index, file) == []


async def test_other_files_are_untouched_by_a_delete(qdrant_index: QdrantIndex) -> None:
    await qdrant_index.ensure_collection()
    user, keep, drop = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await upsert(qdrant_index, user, keep, 2)
    await upsert(qdrant_index, user, drop, 2)

    await qdrant_index.delete_file(user, drop)

    assert len(await points_of(qdrant_index, keep)) == 2


async def test_upsert_rejects_mismatched_chunks_and_vectors(qdrant_index: QdrantIndex) -> None:
    await qdrant_index.ensure_collection()

    with pytest.raises(ValueError, match="one vector per chunk"):
        await qdrant_index.upsert(
            user_id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            filename="a.pdf",
            chunks=chunks(2),
            vectors=[vec(0)],
        )


async def test_the_migrate_step_creates_the_collection(
    qdrant_index: QdrantIndex, settings: Settings
) -> None:
    from app.migrate import setup_qdrant

    configured = settings.model_copy(
        update={"qdrant_url": QDRANT_URL, "qdrant_collection": qdrant_index.alias}
    )

    await setup_qdrant(configured)
    await setup_qdrant(configured)  # a second run is a no-op

    assert (await describe(qdrant_index)).vector_size == DIM
