"""The pass/fail rules of the load test (loadtest/check.py): a gate that cannot fail proves
nothing, so every rule has a run that breaks it."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

CHECK = Path(__file__).resolve().parents[3] / "loadtest" / "check.py"
pytestmark = pytest.mark.skipif(not CHECK.is_file(), reason="repository root not available")


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("loadtest_check", CHECK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load()


def healthy() -> list[Any]:
    row = check.Row
    return [
        row("chat ttft", 400, 0, 120, 4.0),
        row("chat total", 400, 0, 6200, 4.0),
        row("api me", 300, 0, 25, 1.8),
        row("api usage", 150, 0, 30, 0.9),
        row("api conversations", 400, 0, 40, 2.3),
        row("setup register", 25, 0, 400, 0.1),
    ]


def test_a_healthy_run_passes() -> None:
    assert check.evaluate(healthy(), peak_streams=20) == []


def test_a_failed_request_fails_the_run_whatever_its_name() -> None:
    rows = healthy()
    rows.append(check.Row("setup register", 25, 1, 400, 0.1))
    assert any("failed" in p for p in check.evaluate(rows, 20))


def test_a_slow_first_token_fails() -> None:
    rows = [r for r in healthy() if r.name != "chat ttft"]
    rows.append(check.Row("chat ttft", 400, 0, 2001, 4.0))
    assert any("first token" in p for p in check.evaluate(rows, 20))


def test_a_first_token_at_the_limit_passes() -> None:
    rows = [r for r in healthy() if r.name != "chat ttft"]
    rows.append(check.Row("chat ttft", 400, 0, 2000, 4.0))
    assert check.evaluate(rows, 20) == []


def test_a_slow_plain_api_call_fails_and_names_it() -> None:
    rows = [r for r in healthy() if r.name != "api usage"]
    rows.append(check.Row("api usage", 150, 0, 301, 0.9))
    assert any("api usage" in p and "300" in p for p in check.evaluate(rows, 20))


def test_a_slow_setup_call_is_not_an_api_latency_failure() -> None:
    # Registration hashes a password on purpose (argon2); only the `api ` rows have a target.
    rows = [r for r in healthy() if r.name != "setup register"]
    rows.append(check.Row("setup register", 25, 0, 5000, 0.1))
    assert check.evaluate(rows, 20) == []


def test_too_few_streams_open_at_once_fails() -> None:
    assert any("streams" in p for p in check.evaluate(healthy(), peak_streams=12))


def test_too_few_api_requests_per_second_fails() -> None:
    rows = [r for r in healthy() if not r.name.startswith("api ")]
    rows.append(check.Row("api me", 100, 0, 20, 1.0))
    assert any("per second" in p for p in check.evaluate(rows, 20))


def test_a_run_that_measured_nothing_fails_instead_of_passing_empty() -> None:
    assert any("measured nothing" in p for p in check.evaluate([], 20))
    only_api = [r for r in healthy() if r.name.startswith("api ")]
    assert any("measured nothing" in p for p in check.evaluate(only_api, 20))
    no_api = [r for r in healthy() if not r.name.startswith("api ")]
    assert any("no plain API" in p for p in check.evaluate(no_api, 20))


def test_the_csv_reader_reads_what_locust_writes(tmp_path: Path) -> None:
    csv_file = tmp_path / "run_stats.csv"
    csv_file.write_text(
        "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
        "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
        "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n"
        "SSE,chat ttft,159,2,50,60,10,300,0,3.99,0.1,50,55,58,60,70,62,90,99,99,99,99\n"
    )

    (row,) = check.read(csv_file)

    assert (row.name, row.count, row.failures, row.p95_ms, row.rps) == (
        "chat ttft",
        159,
        2,
        62.0,
        3.99,
    )
