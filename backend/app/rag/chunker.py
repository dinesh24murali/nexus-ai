"""Token-aware recursive chunking (doc 08 section 6, step 3).

Text is split on the coarsest boundary that works (paragraph, line, sentence, word, finally raw
characters) into pieces that each fit the limit; pieces are then packed into chunks of at most
`max_tokens`, and every chunk after the first starts with the last `overlap` tokens of the one
before it. Chunks never span pages. Counting uses `o200k_base` through `app.llm.tokens`.
"""

from dataclasses import dataclass

from app.llm.tokens import count_text
from app.rag.parsers import ParsedDocument

MAX_TOKENS = 800
OVERLAP_TOKENS = 120

_SEPARATORS = ("\n\n", "\n", ". ", " ")


@dataclass(frozen=True)
class Chunk:
    index: int  # position in the file, stable for the same content: feeds the Qdrant point id
    page: int | None
    text: str
    tokens: int


def chunk_document(
    document: ParsedDocument, *, max_tokens: int = MAX_TOKENS, overlap: int = OVERLAP_TOKENS
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in document.pages:
        for text in _chunk_text(page.text, max_tokens, min(overlap, max_tokens // 2)):
            chunks.append(Chunk(len(chunks), page.number, text, count_text(text)))
    return chunks


def _chunk_text(text: str, max_tokens: int, overlap: int) -> list[str]:
    if not text.strip():
        return []
    pieces = _split(text, _SEPARATORS, max_tokens)
    sizes = [count_text(p) for p in pieces]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    carried = ""  # the overlap at the head of `current`
    for piece, size in zip(pieces, sizes, strict=True):
        if current_tokens + size > max_tokens and len(current) > (1 if carried else 0):
            chunks.append(_finish(current))
            carried = _tail("".join(current), overlap)
            current = [carried] if carried else []
            current_tokens = count_text(carried)
        if current_tokens + size > max_tokens and carried and len(current) == 1:
            current, current_tokens, carried = [], 0, ""  # the overlap does not fit next to it
        current.append(piece)
        current_tokens += size
    if current and "".join(current).strip() != carried.strip():
        chunks.append(_finish(current))
    return _enforce_limit(chunks, max_tokens)


def _finish(parts: list[str]) -> str:
    return "".join(parts).strip()


def _enforce_limit(chunks: list[str], max_tokens: int) -> list[str]:
    """Summing piece sizes can be a token or two off for the joined text; trim such chunks."""
    out: list[str] = []
    for text in chunks:
        while text and count_text(text) > max_tokens:
            cut = _fit_prefix(text, max_tokens)
            out.append(cut)
            text = text[len(cut) :].strip()
        if text:
            out.append(text)
    return out


def _split(text: str, separators: tuple[str, ...], max_tokens: int) -> list[str]:
    """Pieces that concatenate back to `text`, each within the limit."""
    if count_text(text) <= max_tokens:
        return [text]
    if not separators:
        return _hard_split(text, max_tokens)
    sep, rest = separators[0], separators[1:]
    parts = text.split(sep)
    out: list[str] = []
    for i, part in enumerate(parts):
        piece = part + sep if i < len(parts) - 1 else part
        if piece:
            out.extend(_split(piece, rest, max_tokens))
    return out


def _fit_prefix(text: str, max_tokens: int) -> str:
    """The longest prefix of `text` that fits `max_tokens` (shrinks 10 % at a time; at least one
    character, so progress is guaranteed)."""
    end = min(len(text), max_tokens * 4)  # a token is rarely shorter than a quarter of a character
    while end > 1 and count_text(text[:end]) > max_tokens:
        end = max(1, min(end - 1, int(end * 0.9)))
    return text[:end]


def _hard_split(text: str, max_tokens: int) -> list[str]:
    out = []
    while text:
        piece = _fit_prefix(text, max_tokens)
        out.append(piece)
        text = text[len(piece) :]
    return out


def _tail(text: str, overlap: int) -> str:
    """The last words of `text` worth at most `overlap` tokens ('' when overlap is 0)."""
    if overlap <= 0:
        return ""
    words = text.split()
    taken: list[str] = []
    for word in reversed(words):
        candidate = [word, *taken]
        if count_text(" ".join(candidate)) > overlap:
            break
        taken = candidate
    return (" ".join(taken) + " ") if taken else ""
