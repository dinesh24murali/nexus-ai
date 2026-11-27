"""A stand-in `MemoryManager` for graph tests: memories in a dict, every call recorded."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.graph.intents.manage_memory import Found, SaveOutcome
from app.memory.store import Memory


def memory(text: str, score: float | None = None, *, age_s: int = 0) -> Memory:
    when = datetime(2026, 11, 22, tzinfo=UTC).replace(second=age_s % 60)
    return Memory(
        id=uuid.uuid4(),
        content=text,
        category="fact",
        source="chat",
        conversation_id=None,
        created_at=when,
        updated_at=when,
        last_used_at=when,
        score=score,
    )


class StubManager:
    """Memories of any number of users; `find` answers with `scores` (memory text -> similarity)."""

    def __init__(self, owned: dict[uuid.UUID, list[Memory]] | None = None) -> None:
        self.owned = owned or {}
        self.scores: dict[str, float] = {}
        self.is_enabled = True
        self.calls: list[tuple[str, uuid.UUID]] = []
        self.deleted: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.saved: list[tuple[uuid.UUID, str]] = []
        self.refuse: str | None = None
        self.duplicate = False

    def add(self, user: uuid.UUID, *texts: str) -> list[Memory]:
        made = [memory(t) for t in texts]
        self.owned.setdefault(user, []).extend(made)
        return made

    async def enabled(self, user_id: uuid.UUID) -> bool:
        self.calls.append(("enabled", user_id))
        return self.is_enabled

    async def list(self, user_id: uuid.UUID) -> list[Memory]:
        self.calls.append(("list", user_id))
        return list(self.owned.get(user_id, []))

    async def save(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID | None, content: str
    ) -> SaveOutcome:
        self.calls.append(("save", user_id))
        if self.refuse:
            return SaveOutcome("refused", reason=self.refuse)
        if self.duplicate:
            return SaveOutcome("duplicate")
        self.saved.append((user_id, content))
        self.add(user_id, content)
        return SaveOutcome("created", content=content, embedding_tokens=7)

    async def find(self, user_id: uuid.UUID, query: str) -> Found:
        self.calls.append(("find", user_id))
        found = [
            Memory(**{**m.__dict__, "score": self.scores.get(m.content, 0.0)})
            for m in self.owned.get(user_id, [])
        ]
        found.sort(key=lambda m: m.score or 0.0, reverse=True)
        return Found(found[:3], embedding_tokens=5)

    async def forget(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
        self.calls.append(("forget", user_id))
        mine = self.owned.get(user_id, [])
        hit = next((m for m in mine if m.id == memory_id), None)
        if hit is None:
            return False
        mine.remove(hit)
        self.deleted.append((user_id, memory_id))
        return True

    def texts(self, user_id: uuid.UUID) -> Sequence[str]:
        return [m.content for m in self.owned.get(user_id, [])]


def configurable(user: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {"thread_id": str(uuid.uuid4()), "user_id": str(user), **extra}
