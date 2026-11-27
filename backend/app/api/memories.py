"""Long-term memories: list, add, edit, delete, clear all. The spec is the contract."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import CurrentUserDep, MemoriesDep
from app.api.schemas import MemoryInput, MemoryOut, MemoryPage

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=MemoryPage, summary="List the user's memories")
async def list_memories(
    current: CurrentUserDep,
    memories: MemoriesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> MemoryPage:
    items, next_cursor = await memories.list_page(current.id, limit=limit, cursor=cursor)
    return MemoryPage(items=[MemoryOut.from_memory(m) for m in items], next_cursor=next_cursor)


@router.post("", status_code=201, response_model=MemoryOut, summary="Add a memory manually")
async def create_memory(
    body: MemoryInput, current: CurrentUserDep, memories: MemoriesDep
) -> MemoryOut:
    return MemoryOut.from_memory(await memories.create(current.id, body.content))


@router.delete("", status_code=204, summary="Delete all memories")
async def clear_memories(current: CurrentUserDep, memories: MemoriesDep) -> Response:
    await memories.clear(current.id)
    return Response(status_code=204)


@router.patch("/{memory_id}", response_model=MemoryOut, summary="Edit a memory")
async def update_memory(
    memory_id: uuid.UUID, body: MemoryInput, current: CurrentUserDep, memories: MemoriesDep
) -> MemoryOut:
    return MemoryOut.from_memory(await memories.update(current.id, memory_id, body.content))


@router.delete("/{memory_id}", status_code=204, summary="Delete a memory")
async def delete_memory(
    memory_id: uuid.UUID, current: CurrentUserDep, memories: MemoriesDep
) -> Response:
    await memories.delete(current.id, memory_id)
    return Response(status_code=204)
