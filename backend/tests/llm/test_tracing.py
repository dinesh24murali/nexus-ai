import itertools
import json
from collections.abc import Iterator
from typing import Any, TypedDict

import pytest
from app.core.config import Settings
from app.llm import tracing
from app.llm.fake import FakeChatModel
from app.llm.prompts import load_prompt
from app.llm.tracing import RunTrace, Tracer
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_keys = itertools.count()


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "app_env": "test", "llm_mode": "fake"}
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def trace(**overrides: Any) -> RunTrace:
    base: dict[str, Any] = {
        "name": "chat.turn",
        "conversation_id": "conv-123",
        "user_hash": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "run_id": "run-1",
        "assistant_message_id": "msg-9",
        "prompts": (load_prompt("nexus.system"), load_prompt("nexus.summarize")),
    }
    return RunTrace(**(base | overrides))


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(exporter: InMemorySpanExporter) -> Iterator[Tracer]:
    # The Langfuse client is a per-public-key singleton: use a fresh key for every test.
    n = next(_keys)
    s = settings(
        langfuse_public_key=f"pk-lf-test-{n}",
        langfuse_secret_key=f"sk-lf-test-{n}",
        langfuse_host="http://127.0.0.1:9",  # nothing listens; spans go to the in-memory exporter
    )
    t = Tracer(s, span_exporter=exporter)
    yield t
    t.shutdown()


class _State(TypedDict):
    messages: list[BaseMessage]


def _graph() -> Any:
    async def agent(state: _State) -> _State:
        reply = await FakeChatModel().ainvoke(state["messages"])
        return {"messages": [*state["messages"], reply]}

    builder = StateGraph(_State)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile()


async def run_traced(
    tracer: Tracer, exporter: InMemorySpanExporter, text: str, **trace_overrides: Any
) -> list[ReadableSpan]:
    config = tracer.run_config(trace(**trace_overrides))
    await _graph().ainvoke({"messages": [HumanMessage(text)]}, config=config)
    tracer.flush()
    return list(exporter.get_finished_spans())


def attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


# --- disabled / failure behaviour ---


def test_without_langfuse_keys_tracing_is_off_but_config_is_still_valid() -> None:
    t = Tracer(settings())
    assert t.enabled is False
    config = t.run_config(trace())
    assert "callbacks" not in config or not config["callbacks"]
    assert config["run_name"] == "chat.turn"
    t.flush()  # must not raise
    t.shutdown()


def test_handler_construction_failure_never_reaches_the_run(
    tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**_: Any) -> None:
        raise RuntimeError("langfuse exploded")

    monkeypatch.setattr(tracing, "CallbackHandler", boom)
    config = tracer.run_config(trace())
    assert not config.get("callbacks")


def test_client_construction_failure_disables_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_: Any) -> None:
        raise RuntimeError("bad config")

    monkeypatch.setattr(tracing, "Langfuse", boom)
    t = Tracer(settings(langfuse_public_key="pk-lf-x", langfuse_secret_key="sk-lf-x"))
    assert t.enabled is False


# --- what a trace carries ---


def test_run_config_carries_the_documented_trace_fields(tracer: Tracer) -> None:
    config = tracer.run_config(trace())
    assert config["run_name"] == "chat.turn"
    assert config["callbacks"]
    meta = config["metadata"]
    assert meta["langfuse_user_id"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert meta["langfuse_session_id"] == "conv-123"
    assert meta["langfuse_trace_name"] == "chat.turn"
    assert meta["run_id"] == "run-1"
    assert meta["assistant_message_id"] == "msg-9"
    assert meta["prompt_versions"] == {"nexus.system": 1, "nexus.summarize": 1}
    assert {"env:test", "feature:chat"} <= set(config["tags"])
    assert "langfuse_tags" not in meta  # plain LangChain `tags` already reach the trace


def test_raw_user_id_and_email_never_appear_in_the_config(tracer: Tracer) -> None:
    dump = json.dumps(tracer.run_config(trace()), default=str)
    assert "@" not in dump


async def test_trace_reaches_langfuse_with_user_session_name_and_tags(
    tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    spans = await run_traced(tracer, exporter, "hello")
    root = next(s for s in spans if s.name == "chat.turn")
    a = attrs(root)
    assert a["user.id"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert a["session.id"] == "conv-123"
    assert a["langfuse.trace.name"] == "chat.turn"
    assert "env:test" in a["langfuse.trace.tags"]
    assert json.loads(a["langfuse.trace.metadata.prompt_versions"]) == {
        "nexus.system": 1,
        "nexus.summarize": 1,
    }


async def test_every_graph_node_gets_its_own_span(
    tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    spans = await run_traced(tracer, exporter, "hello")
    kinds = {s.name: attrs(s).get("langfuse.observation.type") for s in spans}
    assert kinds["agent"] == "agent"  # the node span, not only LLM generations
    assert "generation" in kinds.values()


async def test_pii_is_masked_before_export(tracer: Tracer, exporter: InMemorySpanExporter) -> None:
    spans = await run_traced(tracer, exporter, "reach me at jane.doe@example.com or 415-555-0132")
    exported = json.dumps([attrs(s) for s in spans], default=str)
    assert "jane.doe" not in exported
    assert "555-0132" not in exported
    assert "[EMAIL]" in exported


async def test_eval_mode_attaches_no_per_node_callbacks(
    tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    config = tracer.run_config(trace(), mode="eval")
    assert not config.get("callbacks")
    assert config["metadata"]["langfuse_session_id"] == "conv-123"
    await _graph().ainvoke({"messages": [HumanMessage("hi")]}, config=config)
    tracer.flush()
    assert exporter.get_finished_spans() == ()
