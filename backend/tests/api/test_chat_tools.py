"""Web search approval over HTTP: interrupt, resume, reload, abandon (TASK-023).

Real Postgres, the real checkpointer, the fake model and the offline fake web searcher
(`LLM_MODE=fake`), so the flow is the production one without a network.
"""

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from .test_auth import API
from .test_chat import frames, new_conversation, send
from .test_conversations import auth

pytestmark = pytest.mark.integration

WEB = "[[fixture:web_search]]"

Chat = tuple[TestClient, dict[str, str], str]


@pytest.fixture
def chat(client: TestClient, make_user: Any) -> Chat:
    headers = auth(client, make_user)
    return client, headers, new_conversation(client, headers)


def resume(chat: Chat, **body: Any) -> Any:
    client, headers, cid = chat
    return client.post(f"{API}/conversations/{cid}/runs/resume", headers=headers, json=body)


def pause(chat: Chat) -> dict[str, Any]:
    """Send a message that makes the model ask for a web search; returns the interrupt payload."""
    events = frames(send(*chat[:2], chat[2], f"{WEB} what is new in Go?").text)
    assert events[-1]["event"] == "interrupt"
    payload: dict[str, Any] = events[-1]["data"]["interrupt"]
    return payload


def messages(chat: Chat) -> list[dict[str, Any]]:
    client, headers, cid = chat
    page: list[dict[str, Any]] = client.get(
        f"{API}/conversations/{cid}/messages", headers=headers
    ).json()["items"]
    return page


def conversation(chat: Chat) -> dict[str, Any]:
    client, headers, cid = chat
    body: dict[str, Any] = client.get(f"{API}/conversations/{cid}", headers=headers).json()
    return body


def test_a_web_search_ends_the_stream_with_an_interrupt(chat: Chat) -> None:
    client, headers, cid = chat

    r = send(client, headers, cid, f"{WEB} what is new in Go?")

    assert r.status_code == 200
    events = frames(r.text)
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "interrupt" and events[-1]["data"]["type"] == "interrupt"
    assert {"usage", "done"}.isdisjoint(e["event"] for e in events)
    interrupt = events[-1]["data"]["interrupt"]
    assert interrupt["tool"] == "web_search"
    assert interrupt["args"] == {"query": "Go release notes"}
    assert interrupt["interrupt_id"].startswith("int_")
    assert set(interrupt) == {"interrupt_id", "tool", "args", "created_at", "expires_at"}


def test_the_pending_approval_is_on_the_conversation_and_survives_a_reload(chat: Chat) -> None:
    interrupt = pause(chat)

    seen = conversation(chat)

    assert seen["has_active_run"] is False
    assert seen["pending_interrupt"] == interrupt
    # the same, as the sidebar list reports it
    client, headers, _ = chat
    listed = client.get(f"{API}/conversations", headers=headers).json()["items"][0]
    assert listed["pending_interrupt"] == interrupt
    assert [m["role"] for m in messages(chat)] == ["user"]  # no answer yet


def test_approve_streams_the_resumed_run_to_the_answer(chat: Chat) -> None:
    interrupt = pause(chat)

    r = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = frames(r.text)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_started" and events[0]["data"]["user_message_id"] is None
    assert kinds[-2:] == ["usage", "done"]
    assert kinds[1:5] == ["tool_start", "citation", "citation", "tool_end"]
    assert events[1]["data"]["args"] == {"query": "Go release notes"}
    assert events[2]["data"]["citation"]["source_type"] == "web"
    assert conversation(chat)["pending_interrupt"] is None
    answer = messages(chat)[0]
    assert answer["role"] == "assistant" and answer["status"] == "complete"
    assert [(c["name"], c["status"]) for c in answer["tool_calls"]] == [("web_search", "completed")]
    assert [c["source_type"] for c in answer["citations"]] == ["web", "web"]
    assert answer["citations"][0]["url"] == "https://news.example.com/latest"


def test_edit_runs_the_edited_query(chat: Chat) -> None:
    interrupt = pause(chat)

    r = resume(
        chat,
        interrupt_id=interrupt["interrupt_id"],
        decision="edit",
        edited_args={"query": "Go 1.24 generics"},
    )

    assert r.status_code == 200
    events = frames(r.text)
    assert events[1]["data"]["args"] == {"query": "Go 1.24 generics"}
    answer = messages(chat)[0]
    assert answer["tool_calls"][0]["args"] == {"query": "Go 1.24 generics"}
    assert answer["citations"][0]["title"] == "Results for: Go 1.24 generics"


def test_reject_answers_without_searching(chat: Chat) -> None:
    interrupt = pause(chat)

    r = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="reject")

    assert r.status_code == 200
    events = frames(r.text)
    ends = [e["data"] for e in events if e["event"] == "tool_end"]
    assert [e["status"] for e in ends] == ["rejected"]
    assert "citation" not in [e["event"] for e in events]
    answer = messages(chat)[0]
    assert answer["tool_calls"][0]["status"] == "rejected"
    assert answer["citations"] == []


def test_resuming_with_nothing_pending_is_a_409(chat: Chat) -> None:
    r = resume(chat, interrupt_id="int_x", decision="approve")

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NO_PENDING_INTERRUPT"
    assert r.json()["error"]["request_id"].startswith("req_")


def test_a_stale_interrupt_id_is_a_409_and_the_approval_stays(chat: Chat) -> None:
    interrupt = pause(chat)

    r = resume(chat, interrupt_id="int_stale", decision="approve")

    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_PENDING_INTERRUPT"
    assert conversation(chat)["pending_interrupt"] == interrupt


def test_answering_twice_runs_it_once(chat: Chat) -> None:
    interrupt = pause(chat)
    assert (
        resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve").status_code == 200
    )

    again = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "NO_PENDING_INTERRUPT"
    assert [m["role"] for m in messages(chat)] == ["assistant", "user"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"decision": "approve"},
        {"interrupt_id": "int_x"},
        {"interrupt_id": "int_x", "decision": "maybe"},
        {"interrupt_id": "int_x", "decision": "APPROVE"},
        {"interrupt_id": 5, "decision": "approve"},
        {"interrupt_id": "int_x", "decision": "edit", "edited_args": {"query": ""}},
        {"interrupt_id": "int_x", "decision": "edit", "edited_args": {"query": "q" * 401}},
        {"interrupt_id": "int_x", "decision": "approve", "edited_args": {"queries": []}},
        {"interrupt_id": "int_x", "decision": "approve", "edited_args": {"memory_id": "nope"}},
    ],
)
def test_a_malformed_resume_body_is_a_422_that_echoes_nothing(
    chat: Chat, body: dict[str, Any]
) -> None:
    r = resume(chat, **body)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "q" * 50 not in r.text


def test_an_edit_without_a_query_is_a_422_and_the_approval_waits(chat: Chat) -> None:
    interrupt = pause(chat)

    r = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="edit")

    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert conversation(chat)["pending_interrupt"] == interrupt


def test_resume_needs_a_token(client: TestClient) -> None:
    r = client.post(
        f"{API}/conversations/{uuid4()}/runs/resume",
        json={"interrupt_id": "int_x", "decision": "approve"},
    )

    assert r.status_code == 401


def test_unknown_and_malformed_conversation_ids_are_404(client: TestClient, make_user: Any) -> None:
    headers = auth(client, make_user)
    for bad in (str(uuid4()), "not-a-uuid"):
        r = client.post(
            f"{API}/conversations/{bad}/runs/resume",
            headers=headers,
            json={"interrupt_id": "int_x", "decision": "approve"},
        )
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_another_users_approval_cannot_be_answered(
    chat: Chat, client: TestClient, make_user: Any
) -> None:
    interrupt = pause(chat)
    intruder = auth(client, make_user, "intruder@example.com")

    r = client.post(
        f"{API}/conversations/{chat[2]}/runs/resume",
        headers=intruder,
        json={"interrupt_id": interrupt["interrupt_id"], "decision": "approve"},
    )

    assert r.status_code == 404
    assert conversation(chat)["pending_interrupt"] == interrupt


def test_a_new_message_abandons_the_approval(chat: Chat) -> None:
    interrupt = pause(chat)
    client, headers, cid = chat

    r = send(client, headers, cid, "never mind, hello")

    assert frames(r.text)[-1]["event"] == "done"
    assert conversation(chat)["pending_interrupt"] is None
    late = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")
    assert late.status_code == 409


def test_the_calculator_runs_without_an_approval(chat: Chat) -> None:
    client, headers, cid = chat

    r = send(client, headers, cid, "[[fixture:calc]] what is 17.5% of 2348901?")

    assert frames(r.text)[-1]["event"] == "done"
    answer = messages(chat)[0]
    assert [(c["name"], c["status"]) for c in answer["tool_calls"]] == [("calculator", "completed")]
    assert conversation(chat)["pending_interrupt"] is None


def test_a_switched_off_search_is_not_asked_about(chat: Chat, db: psycopg.Connection[Any]) -> None:
    db.execute(
        "INSERT INTO runtime_flags (name, value, updated_by)"
        " VALUES ('TOOLS_DISABLED', 'web_search', 't')"
    )
    client, headers, cid = chat

    r = send(client, headers, cid, f"{WEB} news")

    events = frames(r.text)
    assert events[-1]["event"] == "done"  # no interrupt: the tool is off
    assert conversation(chat)["pending_interrupt"] is None
