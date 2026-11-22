"""Reading memories into a prompt (docs/02_outputs/08 section 7, "Read"; TASK-026).

`load_context` asks for the memories that suit the user's newest message: the 8 closest to it, plus
the 5 most recently used `preference` memories, without repeats and within 600 tokens. Nothing is
read when the user has turned memory off. The memories shown are marked as used, so the 200-memory
cap evicts what the user has not needed for longest.

`select_for_prompt` is pure; `memory_lookup_for` is the production lookup (a database read plus an
embedding of the query, billed to the run). Text is untrusted user data: never logged here.
"""

import uuid
from collections.abc import Callable, Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.repositories import users
from app.graph.nodes.load_context import LoadedMemories, MemoryLookup
from app.llm.tokens import count_text
from app.memory import policy
from app.memory.store import Memory, most_recent, open_memory_store
from app.rag.embedder import Embedder

_log = structlog.get_logger("nexus.memory")

MAX_RELEVANT = 8
MAX_RECENT_PREFERENCES = 5
MAX_MEMORY_TOKENS = 600
BULLET_TOKENS = 2  # "- " and the line break around each memory


def select_for_prompt(
    relevant: Sequence[Memory],
    recent_preferences: Sequence[Memory],
    *,
    max_tokens: int = MAX_MEMORY_TOKENS,
) -> list[Memory]:
    """Relevant memories first (best match first), then recent preferences; each memory once.

    A memory that does not fit in what is left of the budget is skipped, a smaller one after it may
    still fit."""
    candidates = [*relevant[:MAX_RELEVANT]]
    candidates += [m for m in recent_preferences if m.category == "preference"][
        :MAX_RECENT_PREFERENCES
    ]
    chosen: list[Memory] = []
    seen: set[uuid.UUID] = set()
    used = 0
    for memory in candidates:
        if memory.id in seen:
            continue
        cost = count_text(policy.normalise(memory.content)) + BULLET_TOKENS
        if used + cost > max_tokens:
            continue
        seen.add(memory.id)
        chosen.append(memory)
        used += cost
    return chosen


def memory_lookup_for(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    get_embedder: Callable[[], Embedder],
) -> MemoryLookup:
    """The lookup `load_context` uses. The user id is the run's trusted one; the store builds the
    namespace from it, so no other user's memory can be returned. The embedder is built on first
    use, so the API starts without an embeddings key."""

    async def lookup(user_id: uuid.UUID, query: str) -> LoadedMemories:
        async with factory() as session:
            user = await users.get_by_id(session, user_id)
        if user is None or user.status != "active" or not user.memory_enabled:
            return LoadedMemories()

        async with open_memory_store(settings.database_url, get_embedder()) as store:
            everything = await store.all(user_id)  # at most 200: the cap
            if not everything:
                return LoadedMemories()  # nothing to find: do not pay to embed the query
            relevant: list[Memory] = []
            if query.strip():
                relevant = await store.search(user_id, query, limit=MAX_RELEVANT)
            preferences = most_recent(
                everything, limit=MAX_RECENT_PREFERENCES, category="preference"
            )
            chosen = select_for_prompt(relevant, preferences)
            try:
                await store.touch(user_id, [m.id for m in chosen])
            except Exception as exc:  # not worth failing the answer: eviction just sees older use
                _log.warning("memory_touch_failed", error_type=type(exc).__name__)
            return LoadedMemories(
                [policy.normalise(m.content) for m in chosen],
                embedding_tokens=store.embedding_tokens,
            )

    return lookup
