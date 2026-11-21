"""Tenant isolation: one user can never read or change another user's resources.

Architecture section 5: every repository call takes `user_id`; other users' resources are 404
(never 403, which would confirm they exist). This file grows with every user-owned resource.
"""

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from .test_auth import API, login

pytestmark = pytest.mark.integration


@pytest.fixture
def two_users(client: TestClient, make_user: Any) -> tuple[dict[str, str], dict[str, str]]:
    make_user(email="alice@example.com", display_name="Alice")
    make_user(email="bob@example.com", display_name="Bob")
    out = []
    for email in ("alice@example.com", "bob@example.com"):
        token = login(client, email=email).json()["access_token"]
        out.append({"authorization": f"Bearer {token}"})
    return out[0], out[1]


def test_conversations_are_invisible_to_other_users(
    client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
) -> None:
    alice, bob = two_users
    cid = client.post(f"{API}/conversations", headers=alice, json={"title": "Alice secret"}).json()[
        "id"
    ]

    # Bob cannot see it...
    assert client.get(f"{API}/conversations", headers=bob).json()["items"] == []
    r = client.get(f"{API}/conversations/{cid}", headers=bob)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert "Alice" not in r.text

    # ...and the response is indistinguishable from a conversation that never existed.
    ghost = client.get(f"{API}/conversations/{uuid4()}", headers=bob)
    assert {k: v for k, v in r.json()["error"].items() if k != "request_id"} == {
        k: v for k, v in ghost.json()["error"].items() if k != "request_id"
    }


def test_other_users_cannot_rename_or_delete_a_conversation(
    client: TestClient,
    two_users: tuple[dict[str, str], dict[str, str]],
    db: psycopg.Connection[Any],
) -> None:
    alice, bob = two_users
    cid = client.post(f"{API}/conversations", headers=alice, json={"title": "Alice secret"}).json()[
        "id"
    ]

    assert (
        client.patch(
            f"{API}/conversations/{cid}", headers=bob, json={"title": "hijacked"}
        ).status_code
        == 404
    )
    assert client.delete(f"{API}/conversations/{cid}", headers=bob).status_code == 404

    # Untouched for the owner.
    mine = client.get(f"{API}/conversations/{cid}", headers=alice)
    assert mine.status_code == 200 and mine.json()["title"] == "Alice secret"
    assert db.execute("SELECT count(*) FROM conversations").fetchone() == (1,)


def test_pagination_cursors_cannot_be_used_to_peek_at_other_users(
    client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
) -> None:
    """A cursor is just (updated_at, id): replaying Alice's cursor as Bob must not leak rows."""
    alice, bob = two_users
    for i in range(3):
        client.post(f"{API}/conversations", headers=alice, json={"title": f"a{i}"})
    cursor = client.get(f"{API}/conversations", headers=alice, params={"limit": 1}).json()[
        "next_cursor"
    ]
    assert cursor
    leaked = client.get(f"{API}/conversations", headers=bob, params={"cursor": cursor}).json()
    assert leaked == {"items": [], "next_cursor": None}


def test_each_user_only_lists_their_own(
    client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
) -> None:
    alice, bob = two_users
    client.post(f"{API}/conversations", headers=alice, json={"title": "A1"})
    client.post(f"{API}/conversations", headers=bob, json={"title": "B1"})
    client.post(f"{API}/conversations", headers=bob, json={"title": "B2"})
    assert [
        c["title"] for c in client.get(f"{API}/conversations", headers=alice).json()["items"]
    ] == ["A1"]
    assert sorted(
        c["title"] for c in client.get(f"{API}/conversations", headers=bob).json()["items"]
    ) == ["B1", "B2"]
