"""Scoring functions that need no model: plain data in, numbers out."""

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence


def format_compliance(
    output: str, *, must_include: Sequence[str], must_not_include: Sequence[str]
) -> bool:
    """Every required string is present and no forbidden one is (case-insensitive)."""
    text = output.casefold()
    return all(s.casefold() in text for s in must_include) and not any(
        s.casefold() in text for s in must_not_include
    )


def context_recall(
    references: Sequence[tuple[str, int | None]], retrieved: Iterable[tuple[str, int | None]]
) -> float:
    """Share of the reference (file, page) pairs among the retrieved ones. A reference without a
    page (DOCX, TXT, MD) is met by any retrieved passage of that file. A turn where the model never
    searched retrieved nothing, so it scores 0: skipping the search is a retrieval failure too."""
    if not references:
        raise ValueError("a case needs at least one reference")
    got = list(retrieved)
    files = {name for name, _ in got}
    found = sum((ref in got) if ref[1] is not None else (ref[0] in files) for ref in references)
    return found / len(references)


_MARKER = re.compile(r"\[(\d+)\]")


def citation_validity(answer: str, available: Iterable[int]) -> float:
    """Share of the answer's `[n]` markers that point at a passage the tool returned. No marker
    scores 0: an answer built from the files has to cite them. (Whether the cited passage supports
    the sentence is the judged `rag.citation_accuracy`, which is not built.)"""
    markers = [int(m) for m in _MARKER.findall(answer)]
    if not markers:
        return 0.0
    valid = set(available)
    return sum(m in valid for m in markers) / len(markers)


def markers_resolve(text: str, available: Iterable[int]) -> bool:
    """Every `[n]` marker in `text` has an entry among `available`. No marker is fine (a document
    need not cite)."""
    have = set(available)
    return all(int(m) in have for m in _MARKER.findall(text))


def mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100): the value, never an interpolation."""
    if not values:
        return None
    if not 0 < q <= 100:
        raise ValueError("q must be in (0, 100]")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


class Scores:
    """Samples per metric key. A metric is the mean of its samples, unless it is registered as a
    count (sum) or a percentile when read."""

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = defaultdict(list)

    def add(self, key: str, value: float | bool) -> None:
        self._samples[key].append(float(value))

    def extend(self, key: str, values: Iterable[float]) -> None:
        self._samples[key].extend(values)

    def samples(self, key: str) -> list[float]:
        return list(self._samples.get(key, ()))

    def mean(self, key: str) -> float | None:
        return mean(self._samples.get(key, []))

    def total(self, key: str) -> float | None:
        values = self._samples.get(key)
        return math.fsum(values) if values else None

    def percentile(self, key: str, q: float) -> float | None:
        return percentile(self._samples.get(key, []), q)
