import itertools
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.core.config import Settings
from app.llm.tracing import Tracer
from evals.langfuse_log import EvalLogger
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_keys = itertools.count(1000)


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(exporter: InMemorySpanExporter) -> Iterator[Tracer]:
    n = next(_keys)
    s = Settings(
        _env_file=None,
        app_env="test",
        llm_mode="fake",
        langfuse_public_key=f"pk-lf-eval-{n}",
        langfuse_secret_key=f"sk-lf-eval-{n}",
        langfuse_host="http://127.0.0.1:9",
    )
    t = Tracer(s, span_exporter=exporter)
    yield t
    t.shutdown()


def test_without_a_client_everything_is_a_no_op() -> None:
    logger = EvalLogger(None, run_name="r", environment="test")
    assert not logger.enabled
    logger.sync_dataset("chat.golden", "v1", [("c-1", "q", None, {})])
    logger.log_case("chat.golden", "c-1", case_input="q", output="a", scores={"m": 1.0})
    logger.flush()


def test_a_case_becomes_one_trace_with_its_scores(
    tracer: Tracer, exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert tracer.client is not None
    scores: list[dict[str, Any]] = []
    monkeypatch.setattr(tracer.client, "create_score", lambda **kw: scores.append(kw))
    logger = EvalLogger(tracer.client, run_name="smoke-20261121", environment="test")

    logger.log_case(
        "chat.golden",
        "chat-001",
        case_input="Explain errors.Is",
        output="Mail me at someone@example.com",
        scores={"chat.format_compliance": 1.0},
        metadata={"run": 0},
    )
    logger.flush()

    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["eval.chat.golden.chat-001"]  # one span, no node spans
    attrs = dict(spans[0].attributes or {})
    assert attrs["langfuse.trace.name"] == "eval.chat.golden"
    assert attrs["session.id"] == "smoke-20261121"
    assert "dataset:chat.golden" in str(attrs["langfuse.trace.tags"])
    assert "someone@example.com" not in str(attrs)  # masked on the way out (ADR-0004)
    assert [(s["name"], s["value"]) for s in scores] == [("chat.format_compliance", 1.0)]


def test_dataset_items_are_synced_with_stable_ids() -> None:
    client = MagicMock()
    logger = EvalLogger(client, run_name="r", environment="test")
    logger.sync_dataset("chat.golden", "abc123", [("chat-001", "q", {"x": 1}, {"smoke": True})])
    client.create_dataset.assert_called_once_with(
        name="chat.golden", metadata={"version": "abc123"}
    )
    client.create_dataset_item.assert_called_once_with(
        dataset_name="chat.golden",
        id="chat.golden:chat-001",
        input="q",
        expected_output={"x": 1},
        metadata={"smoke": True, "dataset_version": "abc123"},
    )


def test_a_langfuse_outage_never_fails_a_run() -> None:
    client = MagicMock()
    client.create_dataset.side_effect = RuntimeError("down")
    client.start_as_current_observation.side_effect = RuntimeError("down")
    client.flush.side_effect = RuntimeError("down")
    logger = EvalLogger(client, run_name="r", environment="test")
    logger.sync_dataset("chat.golden", "v", [("c-1", "q", None, {})])
    logger.log_case("chat.golden", "c-1", case_input="q", output="a", scores={"m": 1})
    logger.flush()
