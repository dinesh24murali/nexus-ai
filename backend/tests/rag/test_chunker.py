"""Token-aware chunking (TASK-018): 800 tokens with 120 overlap, never across pages."""

from itertools import pairwise

from app.llm.tokens import count_text
from app.rag.chunker import chunk_document
from app.rag.parsers import Page, ParsedDocument


def doc(*pages: tuple[int | None, str]) -> ParsedDocument:
    return ParsedDocument(pages=[Page(n, t) for n, t in pages], page_count=len(pages))


def sentences(n: int, prefix: str = "S") -> str:
    return " ".join(f"{prefix}{i} is a short sentence about topic number {i}." for i in range(n))


def test_a_short_page_is_one_chunk_with_its_page_number() -> None:
    chunks = chunk_document(doc((3, "Hello there.")))

    assert [(c.index, c.page, c.text) for c in chunks] == [(0, 3, "Hello there.")]
    assert chunks[0].tokens == count_text("Hello there.")


def test_every_chunk_fits_the_token_limit() -> None:
    chunks = chunk_document(doc((None, sentences(200))), max_tokens=60, overlap=10)

    assert len(chunks) > 3
    assert all(count_text(c.text) <= 60 for c in chunks)
    assert all(c.tokens == count_text(c.text) for c in chunks)


def test_nothing_is_lost() -> None:
    text = sentences(120)
    chunks = chunk_document(doc((None, text)), max_tokens=60, overlap=10)

    joined = "\n".join(c.text for c in chunks)
    assert all(f"S{i} is a short" in joined for i in range(120))


def test_consecutive_chunks_overlap() -> None:
    chunks = chunk_document(doc((None, sentences(120))), max_tokens=60, overlap=15)

    for before, after in pairwise(chunks):
        tail_words = before.text.split()[-3:]
        assert " ".join(tail_words) in after.text, (before.text, after.text)
        assert after.tokens <= 60


def test_overlap_is_bounded() -> None:
    chunks = chunk_document(doc((None, sentences(120))), max_tokens=60, overlap=15)

    for before, after in pairwise(chunks):
        shared = 0
        words = after.text.split()
        for size in range(1, len(words) + 1):
            if " ".join(words[:size]) in before.text:
                shared = size
        assert count_text(" ".join(words[:shared])) <= 15 + 2


def test_chunks_never_cross_pages() -> None:
    chunks = chunk_document(
        doc((1, sentences(30, "A")), (2, sentences(30, "B"))), max_tokens=60, overlap=10
    )

    assert {c.page for c in chunks} == {1, 2}
    for c in chunks:
        letters = {w[0] for w in c.text.split() if w[0] in "AB" and w[1:2].isdigit()}
        assert letters == ({"A"} if c.page == 1 else {"B"}), c


def test_indexes_are_contiguous_across_pages_and_runs_are_identical() -> None:
    pages = doc((1, sentences(30, "A")), (2, sentences(30, "B")))

    first = chunk_document(pages, max_tokens=60, overlap=10)
    second = chunk_document(pages, max_tokens=60, overlap=10)

    assert [c.index for c in first] == list(range(len(first)))
    assert first == second


def test_paragraph_boundaries_are_preferred_over_mid_sentence_cuts() -> None:
    para_a = " ".join(f"alpha{i}" for i in range(30)) + "."
    para_b = " ".join(f"bravo{i}" for i in range(30)) + "."

    chunks = chunk_document(doc((None, f"{para_a}\n\n{para_b}")), max_tokens=100, overlap=0)

    assert [c.text for c in chunks] == [para_a, para_b]


def test_a_string_without_any_separator_is_still_split_within_the_limit() -> None:
    chunks = chunk_document(doc((None, "x" * 5000)), max_tokens=50, overlap=0)

    assert len(chunks) > 1
    assert all(count_text(c.text) <= 50 for c in chunks)
    assert "".join(c.text for c in chunks) == "x" * 5000


def test_multibyte_text_respects_the_limit() -> None:
    chunks = chunk_document(doc((None, "日本語のテキスト。" * 300)), max_tokens=50, overlap=5)

    assert all(count_text(c.text) <= 50 for c in chunks)


def test_empty_pages_produce_no_chunks() -> None:
    assert chunk_document(doc((1, "   "))) == []


def test_an_overlap_that_does_not_fit_next_to_the_next_piece_is_dropped() -> None:
    text = (
        " ".join(f"word{i}" for i in range(14))
        + ". "
        + " ".join(f"term{i}" for i in range(14))
        + "."
    )

    chunks = chunk_document(doc((None, text)), max_tokens=40, overlap=20)

    assert all(count_text(c.text) <= 40 for c in chunks)
    assert "word0" in chunks[0].text and "term13" in chunks[-1].text


def test_a_chunk_that_is_a_token_too_big_once_joined_is_trimmed() -> None:
    from app.rag.chunker import _enforce_limit

    pieces = _enforce_limit(["x" * 400], 20)

    assert len(pieces) > 1
    assert all(count_text(p) <= 20 for p in pieces)
    assert "".join(pieces) == "x" * 400
