"""The user's memories as a feature (Settings -> Memory; TASK-027): list, add, edit, delete, clear.

Every method takes the caller's id and passes it to the store, which builds the namespace from it,
so another user's memory is simply not found (a 404, like any resource that is not yours). Text
goes through `memory.policy` exactly like the extraction job's, whoever typed it: a memory is read
back into prompts, so it must be a fact about the user, not an instruction, and not a sensitive
category. A refused text is never echoed. Managing works with memory switched off: the switch
only stops new memories from being learned and used.

Adding and editing embed the text, which costs a little: it is recorded in `usage_daily` (not as a
request). Listing and deleting need no embeddings, so they work without an embeddings key.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.repositories import usage
from app.db.repositories.conversations import InvalidCursorError, decode_cursor, encode_cursor
from app.llm.pricing import embedding_cost_usd
from app.memory import policy
from app.memory.extract import DUPLICATE_SIMILARITY
from app.memory.store import (
    MEMORY_CAP,
    LazyEmbedder,
    Memory,
    MemoryStore,
    TextEmbedder,
    open_memory_store,
)

_REASONS = {
    "instruction_like": "A memory must be a fact about you, not an instruction for the assistant.",
    "too_long": "A memory can have at most 500 characters.",
    "empty": "A memory needs some text.",
}
_SENSITIVE = (
    "Nexus does not remember this kind of personal information (health, religion, sexuality, "
    "politics, precise location, credentials or financial identifiers)."
)


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Memory not found.")


def _rejected(reason: str) -> ApiError:
    message = (
        _SENSITIVE if reason.startswith("sensitive:") else _REASONS.get(reason, "Not allowed.")
    )
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "The request body is invalid.",
        details={"fields": [{"loc": ["body", "content"], "msg": message, "type": "value_error"}]},
    )


def _clean(content: str) -> str:
    """The text as it will be stored, or a 422 saying why not (never the text itself)."""
    text = policy.normalise(content)
    if rejection := policy.check(text):
        raise _rejected(rejection.reason)
    return text


class MemoryService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        get_embedder: Callable[[], TextEmbedder],
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._get_embedder = get_embedder

    @asynccontextmanager
    async def _store(self, user_id: uuid.UUID) -> AsyncIterator[MemoryStore]:
        """A store for one call; the embeddings it used are billed to the user afterwards, also
        when the call ends in an error."""
        async with open_memory_store(
            self._settings.database_url, LazyEmbedder(self._get_embedder)
        ) as store:
            try:
                yield store
            finally:
                if store.embedding_tokens:
                    await self._bill(user_id, store.embedding_tokens)

    async def _bill(self, user_id: uuid.UUID, tokens: int) -> None:
        async with self._factory() as session, session.begin():
            await usage.add(
                session,
                user_id,
                datetime.now(UTC).date(),
                prompt_tokens=tokens,
                completion_tokens=0,
                cost_usd=embedding_cost_usd(self._settings.llm_embedding_model, tokens),
            )

    async def list_page(
        self, user_id: uuid.UUID, *, limit: int, cursor: str | None
    ) -> tuple[list[Memory], str | None]:
        """Newest first. The store has no keyset listing, but a user has at most 200 memories, so
        the page is cut from the whole list; the cursor is the usual (time, id) keyset."""
        try:
            after = decode_cursor(cursor) if cursor else None
        except InvalidCursorError:
            raise ApiError(400, "BAD_REQUEST", "Invalid cursor.") from None
        async with self._store(user_id) as store:
            everything = await store.all(user_id)
        ordered = sorted(everything, key=lambda m: (-m.created_at.timestamp(), str(m.id)))
        if after is not None:
            stamp, ident = after
            ordered = [
                m
                for m in ordered
                if m.created_at < stamp or (m.created_at == stamp and str(m.id) > str(ident))
            ]
        page = ordered[:limit]
        more = len(ordered) > limit
        return page, encode_cursor(page[-1].created_at, page[-1].id) if more else None

    async def create(self, user_id: uuid.UUID, content: str) -> Memory:
        text = _clean(content)
        async with self._store(user_id) as store:
            nearest = await store.search(user_id, text, limit=1)
            if nearest and (nearest[0].score or 0.0) >= DUPLICATE_SIMILARITY:
                raise ApiError(409, "MEMORY_DUPLICATE", "You already have a memory like this.")
            memory_id = await store.create(
                user_id, content=text, category="fact", source="manual", conversation_id=None
            )
            await store.evict_to(user_id, MEMORY_CAP)
            created = await store.get(user_id, memory_id)
        if created is None:  # evicted straight away: only possible with a cap below one
            raise _not_found()
        return created

    async def update(self, user_id: uuid.UUID, memory_id: uuid.UUID, content: str) -> Memory:
        text = _clean(content)
        async with self._store(user_id) as store:
            if not await store.update(user_id, memory_id, content=text, category=None):
                raise _not_found()
            updated = await store.get(user_id, memory_id)
        if updated is None:  # deleted between the write and the read
            raise _not_found()
        return updated

    async def delete(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> None:
        async with self._store(user_id) as store:
            if not await store.delete(user_id, memory_id):
                raise _not_found()

    async def clear(self, user_id: uuid.UUID) -> None:
        async with self._store(user_id) as store:
            await store.clear(user_id)
