#!/usr/bin/env python3
"""Compare eval results against evals/eval-baseline.json.

Stack-agnostic regression gate (stdlib only). The project's eval suite must write a
results file shaped like:

    {"metrics": {"rag.faithfulness": 0.91, "latency.ttft_p95_ms": 1200, ...}}

Baseline file shape:

    {
      "tolerance": 0.02,
      "metrics": {
        "rag.faithfulness":    {"threshold": 0.85, "smoke_threshold": 0.80},
        "latency.ttft_p95_ms": {"threshold": 1500, "direction": "lower"}
      }
    }

`direction` defaults to "higher" (higher is better). `tolerance` is an absolute slack
applied to every metric to absorb LLM nondeterminism; a metric may override it with its
own "tolerance". Set "smoke": false on metrics the smoke run does not produce.

Usage: python3 evals/check_baseline.py [--results PATH] [--baseline PATH] [--smoke]
Exit code 0 = pass, 1 = regression, 2 = bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        print(f"error: {path} not found (did the eval suite run?)", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def check(baseline: dict, results: dict, smoke: bool) -> list[str]:
    default_tolerance = float(baseline.get("tolerance", 0.0))
    actual = results.get("metrics", {})
    failures: list[str] = []
    for name, spec in baseline.get("metrics", {}).items():
        if smoke and spec.get("smoke") is False:
            continue
        tolerance = float(spec.get("tolerance", default_tolerance))
        threshold = spec.get("smoke_threshold", spec["threshold"]) if smoke else spec["threshold"]
        direction = spec.get("direction", "higher")
        if name not in actual:
            failures.append(f"{name}: missing from results")
            continue
        value = float(actual[name])
        ok = value >= threshold - tolerance if direction == "higher" else value <= threshold + tolerance
        mark = "PASS" if ok else "FAIL"
        cmp = ">=" if direction == "higher" else "<="
        print(f"[{mark}] {name}: {value:g} (need {cmp} {threshold:g} ± {tolerance:g})")
        if not ok:
            failures.append(f"{name}: {value:g} vs threshold {threshold:g}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=HERE / "results" / "latest.json")
    parser.add_argument("--baseline", type=Path, default=HERE / "eval-baseline.json")
    parser.add_argument("--smoke", action="store_true", help="use smoke_threshold where defined")
    args = parser.parse_args()

    baseline = load(args.baseline)
    if not baseline.get("metrics"):
        print("warning: no metrics in eval-baseline.json yet — gate is a no-op")
        return 0
    failures = check(baseline, load(args.results), args.smoke)
    if failures:
        print(f"\n{len(failures)} eval regression(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nAll eval metrics meet the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
