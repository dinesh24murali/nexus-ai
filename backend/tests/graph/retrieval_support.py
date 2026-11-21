"""A stand-in retriever for graph tests: scripted passages, and a record of who asked what."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.rag.retriever import Passage, Retrieved

FILE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def passage(
    text: str = "Monthly churn decreased from 3.8% to 3.1%.",
    *,
    file_id: uuid.UUID = FILE_ID,
    filename: str = "q3-report.pdf",
    page: int | None = 4,
    score: float = 0.9,
) -> Passage:
    return Passage(file_id=file_id, filename=filename, page=page, text=text, score=score)


@dataclass
class StubRetriever:
    passages: list[Passage] = field(default_factory=lambda: [passage()])
    calls: list[tuple[uuid.UUID, str, Sequence[uuid.UUID] | None]] = field(default_factory=list)
    embedding_tokens: int = 7

    async def search(
        self, user_id: uuid.UUID, query: str, file_ids: Sequence[uuid.UUID] | None = None
    ) -> Retrieved:
        self.calls.append((user_id, query, file_ids))
        return Retrieved(passages=list(self.passages), embedding_tokens=self.embedding_tokens)
