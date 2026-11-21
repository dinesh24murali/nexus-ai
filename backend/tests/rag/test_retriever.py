"""Retrieval against a real Qdrant with the offline fake embeddings (doc 08 section 6)."""

import pytest
from app.llm.tokens import count_text
from app.rag.index import QdrantIndex
from app.rag.retriever import Retriever

from .qdrant_support import ALICE, BOB, embedder, ingest

pytestmark = pytest.mark.integration


def retriever(index: QdrantIndex, **options: int | float) -> Retriever:
    return Retriever(embedder(), index, **options)  # type: ignore[arg-type]


async def test_finds_the_passage_that_answers_the_question(qdrant_index: QdrantIndex) -> None:
    file_id = await ingest(
        qdrant_index,
        ALICE,
        [
            "Monthly churn decreased from 3.8 percent to 3.1 percent in the third quarter",
            "The office relocation to the new building is planned for next spring",
        ],
        filename="q3-report.pdf",
        pages=[1, 2],
    )

    found = await retriever(qdrant_index).search(ALICE, "churn decreased quarter")

    assert found.passages, "expected at least one passage"
    top = found.passages[0]
    assert (top.file_id, top.filename, top.page) == (file_id, "q3-report.pdf", 1)
    assert "churn" in top.text
    assert all("relocation" not in p.text for p in found.passages)


async def test_another_users_chunks_are_never_returned(qdrant_index: QdrantIndex) -> None:
    secret = "The acquisition codename is Bluebird and the price is 40 million"
    alice_file = await ingest(qdrant_index, ALICE, [secret])

    exact = await retriever(qdrant_index).search(BOB, secret)
    named = await retriever(qdrant_index).search(BOB, secret, file_ids=[alice_file])

    assert exact.passages == []
    assert named.passages == []


async def test_the_owner_still_finds_it_with_the_same_query(qdrant_index: QdrantIndex) -> None:
    """The negative control for the isolation test: the data is there and the query matches."""
    secret = "The acquisition codename is Bluebird and the price is 40 million"
    await ingest(qdrant_index, ALICE, [secret])

    assert (await retriever(qdrant_index).search(ALICE, secret)).passages


async def test_file_ids_narrow_the_search_to_those_files(qdrant_index: QdrantIndex) -> None:
    text = "The warranty covers parts and labour for twenty four months"
    first = await ingest(qdrant_index, ALICE, [text], filename="first.pdf")
    second = await ingest(qdrant_index, ALICE, [text], filename="second.pdf")

    found = await retriever(qdrant_index).search(ALICE, text, file_ids=[second])

    assert {p.file_id for p in found.passages} == {second}
    assert first not in {p.file_id for p in found.passages}


async def test_text_below_the_score_threshold_is_dropped(qdrant_index: QdrantIndex) -> None:
    await ingest(qdrant_index, ALICE, ["Quarterly revenue grew in every region"])

    found = await retriever(qdrant_index).search(ALICE, "zebra giraffe antelope savanna")

    assert found.passages == []


async def test_adjacent_chunks_of_one_page_become_one_passage(qdrant_index: QdrantIndex) -> None:
    await ingest(
        qdrant_index,
        ALICE,
        [
            "Refunds are issued within fourteen days of the request",
            "Refunds are issued to the original payment method only",
        ],
        pages=[3, 3],
    )

    found = await retriever(qdrant_index).search(ALICE, "refunds are issued")

    assert len(found.passages) == 1
    assert "fourteen days" in found.passages[0].text
    assert "original payment method" in found.passages[0].text


async def test_chunks_on_different_pages_stay_separate(qdrant_index: QdrantIndex) -> None:
    await ingest(
        qdrant_index,
        ALICE,
        ["Refunds are issued within fourteen days", "Refunds are issued to the original method"],
        pages=[3, 4],
    )

    found = await retriever(qdrant_index).search(ALICE, "refunds are issued")

    assert sorted(p.page for p in found.passages if p.page) == [3, 4]


async def test_at_most_five_passages_are_returned(qdrant_index: QdrantIndex) -> None:
    # Every chunk sits on its own page, so none of them can be merged.
    texts = [f"Shipping policy section {i} covers delivery times" for i in range(8)]
    await ingest(qdrant_index, ALICE, texts, pages=list(range(1, 9)))

    found = await retriever(qdrant_index).search(ALICE, "shipping policy delivery times")

    assert len(found.passages) == 5


async def test_total_text_is_capped_by_the_token_budget(qdrant_index: QdrantIndex) -> None:
    filler = " ".join(["delivery"] * 400)
    texts = [f"shipping {i} {filler}" for i in range(5)]
    await ingest(qdrant_index, ALICE, texts, pages=[1, 2, 3, 4, 5])

    found = await retriever(qdrant_index, max_tokens=1000).search(ALICE, "shipping delivery")

    assert 1 <= len(found.passages) < 5
    assert sum(count_text(p.text) for p in found.passages) <= 1000


async def test_one_passage_larger_than_the_budget_is_still_returned(
    qdrant_index: QdrantIndex,
) -> None:
    await ingest(qdrant_index, ALICE, ["shipping " + " ".join(["delivery"] * 300)])

    found = await retriever(qdrant_index, max_tokens=50).search(ALICE, "shipping delivery")

    assert len(found.passages) == 1


async def test_the_query_embedding_tokens_are_reported(qdrant_index: QdrantIndex) -> None:
    await qdrant_index.ensure_collection()

    found = await retriever(qdrant_index).search(ALICE, "a question of some length here")

    assert found.embedding_tokens > 0
