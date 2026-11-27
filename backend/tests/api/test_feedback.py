"""Rating an answer over HTTP against a real Postgres (TASK-016)."""

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from .test_auth import API
from .test_chat import new_conversation, send
from .test_conversations import auth

pytestmark = pytest.mark.integration


def answer(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    """(conversation id, id of a stored assistant message)."""
    cid = new_conversation(client, headers)
    send(client, headers, cid, "hello")
    items = client.get(f"{API}/conversations/{cid}/messages", headers=headers).json()["items"]
    assistant = next(m for m in items if m["role"] == "assistant")
    return cid, str(assistant["id"])


def rate(client: TestClient, headers: dict[str, str], message_id: str, body: Any) -> Any:
    return client.put(f"{API}/messages/{message_id}/feedback", headers=headers, json=body)


@pytest.fixture
def rated(client: TestClient, make_user: Any) -> tuple[TestClient, dict[str, str], str, str]:
    headers = auth(client, make_user)
    cid, mid = answer(client, headers)
    return client, headers, cid, mid


def test_a_rating_is_stored_and_echoed(
    rated: tuple[TestClient, dict[str, str], str, str], db: psycopg.Connection[Any]
) -> None:
    client, headers, _, mid = rated

    r = rate(client, headers, mid, {"score": -1, "comment": "The second number is wrong."})

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"message_id", "score", "comment", "created_at"}
    assert (body["message_id"], body["score"], body["comment"]) == (
        mid,
        -1,
        "The second number is wrong.",
    )
    row = db.execute("SELECT score, comment FROM feedback WHERE message_id = %s", (mid,)).fetchall()
    assert row == [(-1, "The second number is wrong.")]


def test_the_history_shows_the_rating_and_unrated_messages_show_none(
    rated: tuple[TestClient, dict[str, str], str, str],
) -> None:
    client, headers, cid, mid = rated
    items = client.get(f"{API}/conversations/{cid}/messages", headers=headers).json()["items"]
    assert [m["feedback"] for m in items] == [None, None]

    rate(client, headers, mid, {"score": 1})

    items = client.get(f"{API}/conversations/{cid}/messages", headers=headers).json()["items"]
    by_role = {m["role"]: m["feedback"] for m in items}
    assert by_role == {"assistant": 1, "user": None}


def test_a_second_rating_overwrites_the_first_including_the_comment(
    rated: tuple[TestClient, dict[str, str], str, str], db: psycopg.Connection[Any]
) -> None:
    client, headers, _, mid = rated
    first = rate(client, headers, mid, {"score": -1, "comment": "wrong"}).json()

    second = rate(client, headers, mid, {"score": 1})

    assert second.status_code == 200 and second.json()["comment"] is None
    assert second.json()["created_at"] >= first["created_at"]
    rows = db.execute("SELECT score, comment FROM feedback WHERE message_id = %s", (mid,))
    assert rows.fetchall() == [(1, None)]  # one row per (message, user)


def test_a_blank_comment_is_no_comment(rated: tuple[TestClient, dict[str, str], str, str]) -> None:
    client, headers, _, mid = rated
    assert rate(client, headers, mid, {"score": 1, "comment": "   "}).json()["comment"] is None


def test_another_users_message_is_a_404_and_nothing_is_stored(
    rated: tuple[TestClient, dict[str, str], str, str],
    make_user: Any,
    db: psycopg.Connection[Any],
) -> None:
    client, _, _, mid = rated
    bob = auth(client, make_user, email="bob@example.com")

    r = rate(client, bob, mid, {"score": -1})

    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    ghost = rate(client, bob, str(uuid4()), {"score": -1})
    assert {k: v for k, v in r.json()["error"].items() if k != "request_id"} == {
        k: v for k, v in ghost.json()["error"].items() if k != "request_id"
    }
    assert db.execute("SELECT count(*) FROM feedback").fetchone() == (0,)


def test_bobs_rating_never_replaces_alices(
    rated: tuple[TestClient, dict[str, str], str, str],
    make_user: Any,
) -> None:
    client, alice, cid, mid = rated
    rate(client, alice, mid, {"score": 1})
    bob = auth(client, make_user, email="bob@example.com")
    rate(client, bob, mid, {"score": -1})  # 404: not his message

    items = client.get(f"{API}/conversations/{cid}/messages", headers=alice).json()["items"]
    assert {m["role"]: m["feedback"] for m in items}["assistant"] == 1


def test_only_assistant_messages_can_be_rated(
    rated: tuple[TestClient, dict[str, str], str, str], db: psycopg.Connection[Any]
) -> None:
    client, headers, _, _ = rated
    user_message = db.execute("SELECT id FROM messages WHERE role = 'user'").fetchone()
    assert user_message
    assert rate(client, headers, str(user_message[0]), {"score": 1}).status_code == 404


def test_a_malformed_message_id_is_a_404_not_a_422(
    rated: tuple[TestClient, dict[str, str], str, str],
) -> None:
    client, headers, _, _ = rated
    assert rate(client, headers, "not-a-uuid", {"score": 1}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"score": 0},
        {"score": 2},
        {"score": "1"},
        {"score": True},
        {"score": 1.5},
        {"score": None},
        {"score": 1, "comment": None},
        {"score": 1, "comment": "x" * 1001},
        {"score": 1, "comment": "nul\x00"},
        {"score": 1, "comment": 5},
        [1],
    ],
)
def test_a_body_that_breaks_the_spec_is_422_and_never_echoed(
    rated: tuple[TestClient, dict[str, str], str, str], body: Any
) -> None:
    client, headers, _, mid = rated
    r = rate(client, headers, mid, body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "\x00" not in r.text and "xxxx" not in r.text and "nul\\u0000" not in r.text


def test_rating_needs_a_login(rated: tuple[TestClient, dict[str, str], str, str]) -> None:
    client, _, _, mid = rated
    assert rate(client, {}, mid, {"score": 1}).status_code == 401


def test_deleting_the_conversation_deletes_the_rating(
    rated: tuple[TestClient, dict[str, str], str, str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid, mid = rated
    rate(client, headers, mid, {"score": 1})
    client.delete(f"{API}/conversations/{cid}", headers=headers)
    assert db.execute("SELECT count(*) FROM feedback").fetchone() == (0,)
