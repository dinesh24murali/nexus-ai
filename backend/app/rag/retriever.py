"""Retrieval for `search_my_files` (doc 08 section 6).

Embed the query, ask Qdrant for the nearest chunks of the caller's own files (the tenant filter
lives in `QdrantIndex.search`), merge neighbouring chunks of one page, keep the best few and stop
at a token budget. Passages come back best first.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Protocol

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.llm.factory import build_embeddings_client
from app.llm.tokens import count_text
from app.rag.embedder import Embedder
from app.rag.index import Hit, QdrantIndex

SEARCH_LIMIT = 8
SCORE_THRESHOLD = 0.25
MAX_PASSAGES = 5
MAX_PASSAGE_TOKENS = 3000


@dataclass(frozen=True)
class Passage:
    file_id: uuid.UUID
    filename: str
    page: int | None
    text: str
    score: float


@dataclass(frozen=True)
class Retrieved:
    passages: list[Passage]
    embedding_tokens: int  # billed for the query, so the caller can record the spend


class Searcher(Protocol):
    """What the tool needs from retrieval (tests stand in for it)."""

    async def search(
        self, user_id: uuid.UUID, query: str, file_ids: Sequence[uuid.UUID] | None = None
    ) -> Retrieved: ...


def _merge(hits: Sequence[Hit]) -> list[Passage]:
    """Join runs of consecutive chunks (same file, same page) into one passage. A passage scores
    as its best chunk and the result is ordered best first."""
    ordered = sorted(hits, key=lambda h: (str(h.file_id), h.chunk_index))
    groups: list[list[Hit]] = []
    for hit in ordered:
        last = groups[-1][-1] if groups else None
        if (
            last is not None
            and last.file_id == hit.file_id
            and last.page == hit.page
            and hit.chunk_index == last.chunk_index + 1
        ):
            groups[-1].append(hit)
        else:
            groups.append([hit])
    passages = [
        Passage(
            file_id=group[0].file_id,
            filename=group[0].filename,
            page=group[0].page,
            text="\n\n".join(h.text for h in group),
            score=max(h.score for h in group),
        )
        for group in groups
    ]
    return sorted(passages, key=lambda p: p.score, reverse=True)


class Retriever:
    def __init__(
        self,
        embedder: Embedder,
        index: QdrantIndex,
        *,
        limit: int = SEARCH_LIMIT,
        score_threshold: float = SCORE_THRESHOLD,
        max_passages: int = MAX_PASSAGES,
        max_tokens: int = MAX_PASSAGE_TOKENS,
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._limit = limit
        self._threshold = score_threshold
        self._max_passages = max_passages
        self._max_tokens = max_tokens

    async def search(
        self, user_id: uuid.UUID, query: str, file_ids: Sequence[uuid.UUID] | None = None
    ) -> Retrieved:
        embedded = await self._embedder.embed([query])
        hits = await self._index.search(
            user_id=user_id,
            vector=embedded.vectors[0],
            file_ids=file_ids,
            limit=self._limit,
            score_threshold=self._threshold,
        )
        kept: list[Passage] = []
        used = 0
        for passage in _merge(hits)[: self._max_passages]:
            cost = count_text(passage.text)
            if kept and used + cost > self._max_tokens:
                continue  # a smaller, lower-ranked passage may still fit
            kept.append(passage)
            used += cost
        return Retrieved(passages=kept, embedding_tokens=embedded.tokens)


def lazy_retriever(settings: Settings) -> Callable[[], Retriever]:
    """A getter that builds the production retriever on first use and then reuses it. Nothing is
    built (and no API key is needed) until a run asks for it, so the API starts without one."""

    @cache
    def get() -> Retriever:
        client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() or None,
        )
        return Retriever(
            Embedder(build_embeddings_client(settings), settings.llm_embedding_model),
            QdrantIndex(client, alias=settings.qdrant_collection),
        )

    return get
