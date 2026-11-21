"""SSE framing and event payloads, validated against the OpenAPI spec (the contract)."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import jsonschema_rs
import pytest
import yaml
from app.api import sse

SPEC = Path(__file__).resolve().parents[3] / "docs" / "02_outputs" / "04_api_spec.yaml"


@pytest.fixture(scope="module")
def stream_event_validator() -> Any:
    spec = yaml.safe_load(SPEC.read_text())
    schema = {"$ref": "#/components/schemas/StreamEvent", "components": spec["components"]}
    return jsonschema_rs.validator_for(schema)


def payload(frame: str) -> dict[str, Any]:
    (data_line,) = [ln for ln in frame.splitlines() if ln.startswith("data: ")]
    return json.loads(data_line.removeprefix("data: "))  # type: ignore[no-any-return]


def test_a_frame_carries_event_id_and_json_data_and_ends_with_a_blank_line() -> None:
    frame = sse.frame(sse.TokenEvent(delta="Hi"), 7)

    assert frame == 'event: token\nid: 7\ndata: {"type":"token","delta":"Hi"}\n\n'


def test_data_never_contains_a_raw_newline() -> None:
    frame = sse.frame(sse.TokenEvent(delta="line one\nline two"), 1)

    assert frame.count("\n") == 4  # event, id, data, and the terminating blank line
    assert payload(frame)["delta"] == "line one\nline two"


def test_non_ascii_text_is_preserved() -> None:
    assert payload(sse.frame(sse.TokenEvent(delta="Grüße 你好 🙂"), 1))["delta"] == "Grüße 你好 🙂"


def test_the_ping_is_a_comment_frame_clients_ignore() -> None:
    assert sse.PING == ": ping\n\n"


def test_every_event_we_emit_matches_the_spec(stream_event_validator: Any) -> None:
    events: list[sse.Event] = [
        sse.RunStartedEvent(run_id=uuid4(), user_message_id=uuid4(), assistant_message_id=uuid4()),
        sse.RunStartedEvent(run_id=uuid4(), user_message_id=None, assistant_message_id=uuid4()),
        sse.TokenEvent(delta="x"),
        sse.UsageEvent(
            usage=sse.Usage(
                model="gpt-6.1-terra",
                prompt_tokens=10,
                cached_tokens=4,
                completion_tokens=3,
                cost_usd=Decimal("0.0001"),
            )
        ),
        sse.DoneEvent(message_id=uuid4(), finish_reason="stop"),
        sse.DoneEvent(message_id=uuid4(), finish_reason="cancelled"),
        sse.ErrorEvent(code="LLM_UNAVAILABLE", message="try again", retryable=True),
    ]
    for event in events:
        data = payload(sse.frame(event, 1))
        stream_event_validator.validate(data)
        assert data["type"] == event.type


def test_the_validator_would_reject_a_wrong_payload(stream_event_validator: Any) -> None:
    """Guard against a vacuous check: a malformed event must fail the same validator."""
    with pytest.raises(jsonschema_rs.ValidationError):
        stream_event_validator.validate({"type": "token"})  # `delta` is required
    with pytest.raises(jsonschema_rs.ValidationError):
        stream_event_validator.validate(
            {"type": "done", "message_id": str(uuid4()), "finish_reason": "oops"}
        )
