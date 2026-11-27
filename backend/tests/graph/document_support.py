"""Stand-ins for the document pipeline's graph tests: a `DocumentService` that records calls."""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.documents.draft import DocumentDraft
from app.graph.intents.generate_document import DocumentRef, LastAnswer


class StubDocuments:
    """Messages and answers by (user, conversation); every call is recorded with its user id."""

    def __init__(self) -> None:
        self.messages: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[str, str]]] = {}
        self.answers: dict[tuple[uuid.UUID, uuid.UUID], LastAnswer] = {}
        self.refusal: str | None = None  # what `check` answers
        self.full = False  # `create` finds the day's limit reached
        self.calls: list[tuple[str, uuid.UUID]] = []
        self.checked_kinds: list[str] = []
        self.created: list[tuple[uuid.UUID, dict[str, Any]]] = []

    async def check(self, user_id: uuid.UUID, kind: str) -> str | None:
        self.calls.append(("check", user_id))
        self.checked_kinds.append(kind)
        return self.refusal

    async def last_answer(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> LastAnswer | None:
        self.calls.append(("last_answer", user_id))
        return self.answers.get((user_id, conversation_id))

    async def recent_messages(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
    ) -> list[tuple[str, str]]:
        self.calls.append(("recent_messages", user_id))
        return list(self.messages.get((user_id, conversation_id), []))

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID | None,
        message_id: uuid.UUID | None,
        kind: str,
        draft: DocumentDraft,
    ) -> DocumentRef | None:
        self.calls.append(("create", user_id))
        if self.full:
            return None
        self.created.append((user_id, {"kind": kind, "draft": draft, "message_id": message_id}))
        return DocumentRef(
            id=uuid.uuid4(),
            title=draft.title,
            kind=kind,
            conversation_id=conversation_id,
            message_id=message_id,
            created_at=datetime(2026, 11, 22, tzinfo=UTC),
        )
