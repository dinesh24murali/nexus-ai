"""Embeddings (TASK-018): the fake client, the batching wrapper, the factory and the price."""

import math
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import openai
import pytest
from app.core.config import Settings
from app.llm.factory import LLMConfigError, build_embeddings_client
from app.llm.fake import FakeEmbeddingsClient
from app.llm.pricing import embedding_cost_usd
from app.rag.embedder import BATCH_SIZE, Embedder


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **kw)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_fake_vectors_are_1536_dimensional_unit_length_and_deterministic() -> None:
    client = FakeEmbeddingsClient()

    first = await client.embeddings.create(model="m", input=["quarterly revenue"])
    second = await client.embeddings.create(model="m", input=["quarterly revenue"])

    vector = first.data[0].embedding
    assert len(vector) == 1536
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-6)
    assert vector == second.data[0].embedding


async def test_fake_vectors_put_related_text_closer_than_unrelated_text() -> None:
    client = FakeEmbeddingsClient()
    res = await client.embeddings.create(
        model="m",
        input=[
            "quarterly revenue grew in europe",
            "europe revenue quarterly",
            "cats sleep on sofas",
        ],
    )
    a, b, c = (d.embedding for d in res.data)

    assert cosine(a, b) > cosine(a, c) + 0.3


async def test_fake_client_reports_tokens() -> None:
    res = await FakeEmbeddingsClient().embeddings.create(model="m", input=["one two three", "four"])

    assert res.usage.total_tokens > 0


class RecordingClient:
    """Stands in for the OpenAI SDK to see how the embedder calls it."""

    def __init__(self, *, shuffle: bool = False, drop: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.embeddings = SimpleNamespace(create=self._create)
        self._shuffle = shuffle
        self._drop = drop

    async def _create(self, *, model: str, input: list[str]) -> Any:
        self.calls.append(list(input))
        data = [
            SimpleNamespace(index=i, embedding=[float(len(text)), 0.0])
            for i, text in enumerate(input)
        ]
        if self._shuffle:
            data.reverse()
        if self._drop:
            data = data[: -self._drop]
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=len(input) * 10))


async def test_texts_go_out_in_batches_of_64_and_come_back_in_order() -> None:
    client = RecordingClient()
    texts = ["x" * n for n in range(1, 151)]

    result = await Embedder(client, "text-embedding-3-small").embed(texts)

    assert [len(c) for c in client.calls] == [BATCH_SIZE, BATCH_SIZE, 22]
    assert BATCH_SIZE == 64
    assert [v[0] for v in result.vectors] == [float(n) for n in range(1, 151)]
    assert result.tokens == 1500


async def test_vectors_are_matched_by_index_even_if_the_api_reorders_them() -> None:
    result = await Embedder(RecordingClient(shuffle=True), "m").embed(["a", "bb", "ccc"])

    assert [v[0] for v in result.vectors] == [1.0, 2.0, 3.0]


async def test_a_short_response_is_an_error_not_a_silent_gap() -> None:
    with pytest.raises(RuntimeError, match="expected 3 embeddings, got 2"):
        await Embedder(RecordingClient(drop=1), "m").embed(["a", "bb", "ccc"])


async def test_no_texts_means_no_calls() -> None:
    client = RecordingClient()

    result = await Embedder(client, "m").embed([])

    assert result.vectors == [] and result.tokens == 0 and client.calls == []


def test_factory_returns_the_fake_in_fake_mode() -> None:
    assert isinstance(build_embeddings_client(settings(llm_mode="fake")), FakeEmbeddingsClient)


def test_factory_refuses_a_missing_key() -> None:
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        build_embeddings_client(settings(llm_mode="real", openai_api_key=""))


def test_factory_builds_the_sdk_client_with_a_30s_timeout_and_3_retries() -> None:
    client = build_embeddings_client(settings(llm_mode="real", openai_api_key="sk-test"))

    assert isinstance(client, openai.AsyncOpenAI)
    assert client.max_retries == 3
    assert client.timeout == 30


def test_embedding_cost_is_two_cents_per_million_tokens() -> None:
    assert embedding_cost_usd("text-embedding-3-small", 1_000_000) == Decimal("0.02")
    assert embedding_cost_usd("text-embedding-3-small", 0) == Decimal(0)


async def test_fake_text_without_words_still_gets_a_unit_vector() -> None:
    res = await FakeEmbeddingsClient().embeddings.create(model="m", input=["", "!!! ???"])

    for item in res.data:
        assert item.embedding[0] == 1.0 and sum(x * x for x in item.embedding) == 1.0
