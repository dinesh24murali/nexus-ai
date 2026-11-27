"""Chat endpoints over HTTP against a real Postgres, the real checkpointer and the fake model."""

import json
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from .test_auth import API
from .test_conversations import auth, user_id

pytestmark = pytest.mark.integration


def new_conversation(client: TestClient, headers: dict[str, str]) -> str:
    return str(client.post(f"{API}/conversations", headers=headers, json={}).json()["id"])


def frames(body: str) -> list[dict[str, Any]]:
    """Parse an SSE body into [{event, id, data}] (the way a client must)."""
    out: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        out.append(
            {"event": fields["event"], "id": int(fields["id"]), "data": json.loads(fields["data"])}
        )
    return out


def send(client: TestClient, headers: dict[str, str], cid: str, text: str) -> Any:
    return client.post(
        f"{API}/conversations/{cid}/messages", headers=headers, json={"content": text}
    )


@pytest.fixture
def chat(client: TestClient, make_user: Any) -> tuple[TestClient, dict[str, str], str]:
    headers = auth(client, make_user)
    return client, headers, new_conversation(client, headers)


def test_sending_a_message_streams_typed_events_in_order(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat

    r = send(client, headers, cid, "hello")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    events = frames(r.text)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_started"
    assert kinds[-2:] == ["usage", "done"]
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))
    assert all(e["event"] == e["data"]["type"] for e in events)
    text = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert text == "Fake reply: hello"
    done = events[-1]["data"]
    row = db.execute(
        "SELECT content, status FROM messages WHERE id = %s", (done["message_id"],)
    ).fetchone()
    assert row == ("Fake reply: hello", "complete")


def test_the_conversation_shows_the_run_and_the_new_message_afterwards(
    chat: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "hello")

    conv = client.get(f"{API}/conversations/{cid}", headers=headers).json()
    assert conv["has_active_run"] is False

    page = client.get(f"{API}/conversations/{cid}/messages", headers=headers).json()
    assert [m["role"] for m in page["items"]] == ["assistant", "user"]  # newest first
    answer, question = page["items"]
    assert question["content"] == "hello" and "usage" not in question
    assert answer["content"] == "Fake reply: hello"
    assert (answer["status"], answer["finish_reason"]) == ("complete", "stop")
    assert answer["usage"]["prompt_tokens"] > 0 and answer["usage"]["cost_usd"] > 0
    assert answer["tool_calls"] == [] and answer["citations"] == []
    assert page["next_cursor"] is None


def test_messages_page_with_a_cursor(chat: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, cid = chat
    for text in ("one", "two"):
        send(client, headers, cid, text)

    first = client.get(f"{API}/conversations/{cid}/messages?limit=3", headers=headers).json()
    assert [m["content"] for m in first["items"]] == ["Fake reply: two", "two", "Fake reply: one"]
    second = client.get(
        f"{API}/conversations/{cid}/messages?limit=3&cursor={first['next_cursor']}", headers=headers
    ).json()
    assert [m["content"] for m in second["items"]] == ["one"]
    assert second["next_cursor"] is None


def test_a_bad_messages_cursor_is_a_400(chat: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, cid = chat
    r = client.get(f"{API}/conversations/{cid}/messages?cursor=garbage", headers=headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.parametrize("path", ["messages", "regenerate", "runs/cancel"])
def test_chat_endpoints_require_a_token(client: TestClient, path: str) -> None:
    r = client.post(f"{API}/conversations/{uuid4()}/{path}", json={"content": "x"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["messages", "regenerate", "runs/cancel"])
def test_unknown_and_malformed_conversation_ids_are_404(
    client: TestClient, make_user: Any, path: str
) -> None:
    headers = auth(client, make_user)
    for bad in (str(uuid4()), "not-a-uuid"):
        r = client.post(f"{API}/conversations/{bad}/{path}", headers=headers, json={"content": "x"})
        assert r.status_code == 404, (path, bad)
        assert r.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"content": ""},
        {"content": "   \n\t "},
        {"content": "has\x00nul"},
        {"content": "x" * 16_001},
        {"content": 5},
        {"content": None},
    ],
)
def test_invalid_message_bodies_are_422_without_echoing_the_input(
    chat: tuple[TestClient, dict[str, str], str], body: dict[str, Any], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat

    r = client.post(f"{API}/conversations/{cid}/messages", headers=headers, json=body)

    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "xxxxxxxx" not in r.text
    assert db.execute("SELECT count(*) FROM messages").fetchone() == (0,)


def test_the_longest_allowed_message_is_accepted(
    chat: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, cid = chat
    assert send(client, headers, cid, "x" * 16_000).status_code == 200


def test_a_second_send_during_a_run_is_409_run_in_progress(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat
    db.execute(
        "UPDATE conversations SET active_run_id = gen_random_uuid(), run_heartbeat_at = now()"
    )

    r = send(client, headers, cid, "hi")

    assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_IN_PROGRESS"
    assert db.execute("SELECT count(*) FROM messages").fetchone() == (0,)


def test_regenerate_replaces_the_answer(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "hello")
    old = db.execute("SELECT id FROM messages WHERE role = 'assistant'").fetchone()

    r = client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)

    assert r.status_code == 200
    events = frames(r.text)
    assert events[0]["data"]["user_message_id"] is None
    assert events[-1]["event"] == "done"
    new = db.execute("SELECT id FROM messages WHERE role = 'assistant'").fetchall()
    assert len(new) == 1 and new[0] != old


def test_regenerate_without_an_answer_is_409(chat: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, cid = chat
    r = client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REGENERATE_NOT_SUPPORTED"


def test_cancel_with_nothing_running_is_202(chat: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, cid = chat
    assert client.post(f"{API}/conversations/{cid}/runs/cancel", headers=headers).status_code == 202


def test_a_draining_server_refuses_new_runs_with_retry_after(
    chat: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, cid = chat
    client.app.state.runs.begin_drain()  # type: ignore[attr-defined]

    r = send(client, headers, cid, "hi")

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SERVICE_RESTARTING"
    assert int(r.headers["retry-after"]) > 0
    # reading history still works while draining
    assert client.get(f"{API}/conversations/{cid}/messages", headers=headers).status_code == 200


def test_the_title_job_is_queued_after_the_first_exchange(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "hello")

    row = db.execute("SELECT kind, status, user_id FROM jobs").fetchone()
    assert row and row[:2] == ("generate_title", "queued")
    assert str(row[2]) == user_id(db)
