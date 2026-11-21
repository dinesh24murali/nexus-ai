"""The SSE stream against the OpenAPI spec: every frame must be a valid `StreamEvent` and the
events must arrive in the documented order (docs/02_outputs/04_api_spec.yaml, `EventStream`).

schemathesis cannot do this: it generates requests for operations, but a successful send needs a
conversation the caller owns, and the response is a stream rather than one JSON body.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jsonschema_rs
import pytest
import yaml

pytestmark = pytest.mark.integration

SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "docs" / "02_outputs" / "04_api_spec.yaml").read_text()
)
VALIDATOR = jsonschema_rs.validator_for(
    {"$ref": "#/components/schemas/StreamEvent", "components": SPEC["components"]}
)
# Spec order: run_started, [route_selected], token/tool/citation/..., then usage + done, or error.
BODY_EVENTS = {
    "token",
    "tool_start",
    "tool_end",
    "citation",
    "memory_updated",
    "progress",
    "document_created",
}


def parse(body: str) -> list[tuple[str, int, dict[str, Any]]]:
    frames = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((fields["event"], int(fields["id"]), json.loads(fields["data"])))
    return frames


@pytest.fixture
def http(live_server_url: str, make_user: Any) -> tuple[str, httpx.Client, str]:
    make_user()
    client = httpx.Client(timeout=30)
    token = client.post(
        f"{live_server_url}/auth/login",
        json={"email": "marco@example.com", "password": "correct-horse-battery"},
    ).json()["access_token"]
    client.headers["authorization"] = f"Bearer {token}"
    cid = client.post(f"{live_server_url}/conversations", json={}).json()["id"]
    return live_server_url, client, cid


def check_stream(response: httpx.Response) -> list[tuple[str, int, dict[str, Any]]]:
    declared = SPEC["paths"]["/conversations/{conversationId}/messages"]["post"]["responses"]["200"]
    assert declared == {"$ref": "#/components/responses/EventStream"}
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = parse(response.text)
    for event, _seq, data in frames:
        VALIDATOR.validate(data)  # raises with the offending payload if the spec is violated
        assert data["type"] == event, "the SSE `event:` field equals the payload `type`"
    assert [seq for _, seq, _ in frames] == list(range(1, len(frames) + 1))
    return frames


def test_a_send_stream_follows_the_documented_event_order(
    http: tuple[str, httpx.Client, str],
) -> None:
    base, client, cid = http

    r = client.post(f"{base}/conversations/{cid}/messages", json={"content": "hello"})

    kinds = [event for event, _, _ in check_stream(r)]
    assert kinds[0] == "run_started"
    assert kinds[-2:] == ["usage", "done"]
    assert set(kinds[1:-2]) <= BODY_EVENTS and "token" in kinds
    assert kinds.count("run_started") == kinds.count("done") == kinds.count("usage") == 1


def test_a_regenerate_stream_is_a_valid_stream_too(http: tuple[str, httpx.Client, str]) -> None:
    base, client, cid = http
    client.post(f"{base}/conversations/{cid}/messages", json={"content": "hello"})

    r = client.post(f"{base}/conversations/{cid}/regenerate")

    frames = check_stream_regenerate(r)
    assert frames[0][2]["user_message_id"] is None


def check_stream_regenerate(response: httpx.Response) -> list[tuple[str, int, dict[str, Any]]]:
    assert response.status_code == 200
    frames = parse(response.text)
    for _event, _seq, data in frames:
        VALIDATOR.validate(data)
    assert [e for e, _, _ in frames][-1] == "done"
    return frames


def test_the_stream_ends_with_an_error_event_when_the_model_fails(
    live_server: Callable[..., str], make_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import fake

    def boom(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("model blew up")

    monkeypatch.setattr(fake.FakeChatModel, "_plan", boom)
    base = live_server()
    make_user()
    with httpx.Client(timeout=30) as client:
        token = client.post(
            f"{base}/auth/login",
            json={"email": "marco@example.com", "password": "correct-horse-battery"},
        ).json()["access_token"]
        client.headers["authorization"] = f"Bearer {token}"
        cid = client.post(f"{base}/conversations", json={}).json()["id"]
        r = client.post(f"{base}/conversations/{cid}/messages", json={"content": "hello"})

    frames = parse(r.text)
    for _event, _seq, data in frames:
        VALIDATOR.validate(data)
    assert [e for e, _, _ in frames] == ["run_started", "error"]
    assert frames[-1][2]["code"] == "INTERNAL" and "blew up" not in r.text


# ---------------------------------------------------------------- refusals before the stream opens

ERROR_VALIDATOR = jsonschema_rs.validator_for(
    {"$ref": "#/components/schemas/Error", "components": SPEC["components"]}
)


def test_a_moderation_refusal_is_a_documented_400_error(
    http: tuple[str, httpx.Client, str],
) -> None:
    base, client, cid = http

    r = client.post(f"{base}/conversations/{cid}/messages", json={"content": "[[moderation:flag]]"})

    assert (
        r.status_code == 400
        and "400" in SPEC["paths"]["/conversations/{conversationId}/messages"]["post"]["responses"]
    )
    ERROR_VALIDATOR.validate(r.json())
    assert r.json()["error"]["code"] == "MODERATION_FLAGGED"


def test_a_budget_refusal_is_a_documented_429_with_retry_after_and_scope(
    http: tuple[str, httpx.Client, str], db: Any, make_user: Any
) -> None:
    base, client, cid = http
    db.execute(
        "INSERT INTO usage_daily (user_id, day, cost_usd)"
        " SELECT id, (now() AT TIME ZONE 'UTC')::date, 99 FROM users"
    )

    r = client.post(f"{base}/conversations/{cid}/messages", json={"content": "hello"})

    assert r.status_code == 429
    responses = SPEC["paths"]["/conversations/{conversationId}/messages"]["post"]["responses"]
    assert "429" in responses
    ERROR_VALIDATOR.validate(r.json())
    assert r.json()["error"]["details"]["scope"] in {"user", "global"}
    assert int(r.headers["retry-after"]) > 0


# ---------------------------------------------------------------- tool approval (resume)

RESUME = "/conversations/{conversationId}/runs/resume"


def test_a_paused_stream_ends_with_a_documented_interrupt_and_its_resume_stream_completes(
    http: tuple[str, httpx.Client, str],
) -> None:
    base, client, cid = http

    first = client.post(
        f"{base}/conversations/{cid}/messages", json={"content": "[[fixture:web_search]] news"}
    )

    frames = parse(first.text)
    for _event, _seq, data in frames:
        VALIDATOR.validate(data)
    kinds = [event for event, _, _ in frames]
    assert kinds[0] == "run_started" and kinds[-1] == "interrupt"
    assert set(kinds[1:-1]) <= BODY_EVENTS  # nothing after the interrupt, no usage or done
    interrupt = frames[-1][2]["interrupt"]

    second = client.post(
        f"{base}/conversations/{cid}/runs/resume",
        json={"interrupt_id": interrupt["interrupt_id"], "decision": "approve"},
    )

    assert SPEC["paths"][RESUME]["post"]["responses"]["200"] == {
        "$ref": "#/components/responses/EventStream"
    }
    assert second.status_code == 200
    assert second.headers["content-type"].startswith("text/event-stream")
    resumed = parse(second.text)
    for event, _seq, data in resumed:
        VALIDATOR.validate(data)
        assert data["type"] == event
    kinds = [event for event, _, _ in resumed]
    assert kinds[0] == "run_started" and resumed[0][2]["user_message_id"] is None
    assert kinds[-2:] == ["usage", "done"]
    assert set(kinds[1:-2]) <= BODY_EVENTS and "citation" in kinds


def test_the_resume_refusals_are_documented_errors(http: tuple[str, httpx.Client, str]) -> None:
    base, client, cid = http
    documented = SPEC["paths"][RESUME]["post"]["responses"]

    nothing_pending = client.post(
        f"{base}/conversations/{cid}/runs/resume",
        json={"interrupt_id": "int_x", "decision": "approve"},
    )
    malformed = client.post(
        f"{base}/conversations/{cid}/runs/resume", json={"interrupt_id": "int_x"}
    )

    for response, status in ((nothing_pending, 409), (malformed, 422)):
        assert response.status_code == status and str(status) in documented
        ERROR_VALIDATOR.validate(response.json())
    assert nothing_pending.json()["error"]["code"] == "NO_PENDING_INTERRUPT"
    assert "429" in documented  # a resume starts a run, so it can be rate limited like a send
