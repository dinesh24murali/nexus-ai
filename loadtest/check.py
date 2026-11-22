#!/usr/bin/env python3
"""Pass/fail for a load run (TASK-031), from the CSV that Locust wrote.

    python3 check.py results/run_stats.csv results/peak.json

The targets are the non-functional requirements in docs/02_outputs/01_prd.md: time to first token
p95 under 2 s (the model is scripted, so this is the app's own overhead), plain API calls p95 under
300 ms, nothing failing, and the load really was the target load. Standard library only.
"""

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

TTFT_P95_MS = 2000
API_P95_MS = 300
MIN_API_RPS = 4.5  # the target is 5 requests per second
TARGET_STREAMS = 20


@dataclass(frozen=True)
class Row:
    name: str
    count: int
    failures: int
    p95_ms: float
    rps: float


def read(path: Path) -> list[Row]:
    with path.open(newline="") as handle:
        return [
            Row(
                name=r["Name"],
                count=int(r["Request Count"]),
                failures=int(r["Failure Count"]),
                p95_ms=float(r["95%"] or 0),
                rps=float(r["Requests/s"] or 0),
            )
            for r in csv.DictReader(handle)
        ]


def evaluate(rows: list[Row], peak_streams: int) -> list[str]:
    """What is wrong with this run; an empty list is a pass."""
    problems: list[str] = []
    by_name = {r.name: r for r in rows}

    failed = [f"{r.name}: {r.failures} of {r.count}" for r in rows if r.failures]
    if failed:
        problems.append("requests failed: " + "; ".join(failed))

    ttft = by_name.get("chat ttft")
    total = by_name.get("chat total")
    if not ttft or ttft.count == 0 or not total or total.count == 0:
        problems.append("no chat stream finished: the run measured nothing")
    else:
        if ttft.p95_ms > TTFT_P95_MS:
            problems.append(f"time to first token p95 {ttft.p95_ms:.0f} ms > {TTFT_P95_MS} ms")
        if peak_streams < TARGET_STREAMS:
            problems.append(
                f"at most {peak_streams} streams were open at once, the target is {TARGET_STREAMS}"
            )

    api = [r for r in rows if r.name.startswith("api ")]
    if not api or sum(r.count for r in api) == 0:
        problems.append("no plain API request was made")
    else:
        for r in api:
            if r.p95_ms > API_P95_MS:
                problems.append(f"{r.name} p95 {r.p95_ms:.0f} ms > {API_P95_MS} ms")
        rps = sum(r.rps for r in api)
        if rps < MIN_API_RPS:
            problems.append(f"only {rps:.1f} API requests per second, the target is 5")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stats_csv", type=Path)
    parser.add_argument("peak_file", type=Path, help="JSON with the peak of open streams")
    args = parser.parse_args()

    rows = read(args.stats_csv)
    peak = int(json.loads(args.peak_file.read_text())["peak_streams"])
    print(f"peak open streams: {peak}")
    print(f"{'name':<22}{'count':>8}{'fail':>6}{'p95 ms':>9}{'req/s':>8}")
    for r in rows:
        print(f"{r.name:<22}{r.count:>8}{r.failures:>6}{r.p95_ms:>9.0f}{r.rps:>8.2f}")
    problems = evaluate(rows, peak)
    for problem in problems:
        print(f"FAIL: {problem}")
    print("PASS" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
