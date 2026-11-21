"""Scoring functions that need no model: plain data in, numbers out."""

import math
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
