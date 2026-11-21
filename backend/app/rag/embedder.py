"""Batched embeddings (doc 08 section 6, step 4). The client comes from `llm.factory`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

BATCH_SIZE = 64  # doc 08 section 3


@dataclass(frozen=True)
class Embedded:
    vectors: list[list[float]]
    tokens: int  # billed input tokens, for the spend record


class Embedder:
    def __init__(self, client: Any, model: str, *, batch_size: int = BATCH_SIZE) -> None:
        self._client = client
        self._model = model
        self._batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> Embedded:
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            response = await self._client.embeddings.create(model=self._model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(batch):
                raise RuntimeError(
                    f"embeddings API returned a wrong count: expected {len(batch)} embeddings, "
                    f"got {len(ordered)}"
                )
            vectors.extend([float(x) for x in item.embedding] for item in ordered)
            tokens += int(response.usage.total_tokens)
        return Embedded(vectors=vectors, tokens=tokens)
