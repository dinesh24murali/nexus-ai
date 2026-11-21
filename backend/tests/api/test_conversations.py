"""Conversations CRUD against a real Postgres."""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from .test_auth import API, login

pytestmark = pytest.mark.integration

TS = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def auth(client: TestClient, make_user: Any, email: str = "marco@example.com") -> dict[str, str]:
    make_user(email=email)
    token = login(client, email=email).json()["access_token"]
    return {"authorization": f"Bearer {token}"}


def user_id(db: psycopg.Connection[Any], email: str = "marco@example.com") -> str:
    row = db.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    assert row
    return str(row[0])


def seed(
    db: psycopg.Connection[Any],
    owner: str,
    *,
    title: str = "t",
    updated_at: datetime = TS,
    **cols: Any,
) -> str:
    """Insert a conversation directly with a chosen updated_at; returns its id."""
    names = ["user_id", "title", "created_at", "updated_at", *cols]
    values = [owner, title, updated_at, updated_at, *cols.values()]
    row = db.execute(
        f"INSERT INTO conversations ({', '.join(names)}) "  # noqa: S608 - fixed column names
        f"VALUES ({', '.join(['%s'] * len(values))}) RETURNING id",
        values,
    ).fetchone()
    assert row
    return str(row[0])


# ───────────────────────────── create ─────────────────────────────


def test_create_defaults_to_new_chat(client: TestClient, make_user: Any) -> None:
    h = auth(client, make_user)
    r = client.post(f"{API}/conversations", headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "New chat"
    assert body["has_active_run"] is False
    assert body["pending_interrupt"] is None
    assert set(body) == {
        "id",
        "title",
        "has_active_run",
        "pending_interrupt",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize("payload", [{}, {"title": "  Trip planning  "}])
def test_create_accepts_an_optional_trimmed_title(
    client: TestClient, make_user: Any, payload: dict[str, str]
) -> None:
    h = auth(client, make_user)
    r = client.post(f"{API}/conversations", headers=h, json=payload)
    assert r.status_code == 201
    assert r.json()["title"] == (payload.get("title", "New chat")).strip()


@pytest.mark.parametrize("title", ["", "   ", "x" * 101, None, 5])
def test_create_validates_title(client: TestClient, make_user: Any, title: Any) -> None:
    h = auth(client, make_user)
    r = client.post(f"{API}/conversations", headers=h, json={"title": title})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_create_title_boundary_100_chars_ok(client: TestClient, make_user: Any) -> None:
    h = auth(client, make_user)
    assert (
        client.post(f"{API}/conversations", headers=h, json={"title": "x" * 100}).status_code == 201
    )


# ───────────────────────────── list & pagination ─────────────────────────────


def test_list_is_empty_for_a_new_user(client: TestClient, make_user: Any) -> None:
    h = auth(client, make_user)
    r = client.get(f"{API}/conversations", headers=h)
    assert r.status_code == 200 and r.json() == {"items": [], "next_cursor": None}


def test_list_is_most_recently_updated_first(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    h = auth(client, make_user)
    me = user_id(db)
    old = seed(db, me, title="old", updated_at=TS)
    new = seed(db, me, title="new", updated_at=TS + timedelta(hours=2))
    mid = seed(db, me, title="mid", updated_at=TS + timedelta(hours=1))
    ids = [c["id"] for c in client.get(f"{API}/conversations", headers=h).json()["items"]]
    assert ids == [new, mid, old]


def test_pagination_walks_every_item_exactly_once(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    h = auth(client, make_user)
    me = user_id(db)
    expected = [seed(db, me, updated_at=TS + timedelta(minutes=i)) for i in range(7)][::-1]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 3} | ({"cursor": cursor} if cursor else {})
        page = client.get(f"{API}/conversations", headers=h, params=params).json()
        assert len(page["items"]) <= 3
        seen += [c["id"] for c in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == expected
    assert pages == 3  # 3 + 3 + 1


def test_exact_page_boundary_has_no_phantom_next_page(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    h = auth(client, make_user)
    me = user_id(db)
    for i in range(4):
        seed(db, me, updated_at=TS + timedelta(minutes=i))
    page = client.get(f"{API}/conversations", headers=h, params={"limit": 4}).json()
    assert len(page["items"]) == 4 and page["next_cursor"] is None


def test_ties_on_updated_at_are_paginated_without_gaps_or_duplicates(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    h = auth(client, make_user)
    me = user_id(db)
    ids = {seed(db, me, updated_at=TS) for _ in range(9)}  # identical timestamps
    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = client.get(f"{API}/conversations", headers=h, params=params).json()
        seen += [c["id"] for c in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 9 and set(seen) == ids


def test_new_activity_between_pages_does_not_duplicate_or_skip_older_items(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    """Keyset pagination: a conversation updated after page 1 was fetched must not shift page 2."""
    h = auth(client, make_user)
    me = user_id(db)
    ids = [seed(db, me, updated_at=TS + timedelta(minutes=i)) for i in range(6)][::-1]
    first = client.get(f"{API}/conversations", headers=h, params={"limit": 3}).json()
    seed(db, me, title="arrived later", updated_at=TS + timedelta(days=1))  # lands before page 1
    second = client.get(
        f"{API}/conversations", headers=h, params={"limit": 3, "cursor": first["next_cursor"]}
    ).json()
    assert [c["id"] for c in first["items"]] + [c["id"] for c in second["items"]] == ids


def test_default_limit_is_30(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    h = auth(client, make_user)
    me = user_id(db)
    for i in range(32):
        seed(db, me, updated_at=TS + timedelta(minutes=i))
    page = client.get(f"{API}/conversations", headers=h).json()
    assert len(page["items"]) == 30 and page["next_cursor"] is not None


@pytest.mark.parametrize("limit", ["0", "-1", "101", "abc", "1.5"])
def test_list_rejects_out_of_range_limit_with_422(
    client: TestClient, make_user: Any, limit: str
) -> None:
    h = auth(client, make_user)
    r = client.get(f"{API}/conversations", headers=h, params={"limit": limit})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "garbage",
        "!!!not-base64!!!",
        _b64("no-separator"),
        _b64("not-a-date|" + str(uuid4())),
        _b64("2026-11-01T12:00:00+00:00|not-a-uuid"),
        _b64("2026-11-01T12:00:00|" + str(uuid4())),  # naive timestamp
    ],
)
def test_list_rejects_a_malformed_cursor_with_400(
    client: TestClient, make_user: Any, cursor: str
) -> None:
    h = auth(client, make_user)
    r = client.get(f"{API}/conversations", headers=h, params={"cursor": cursor})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_REQUEST"


def test_an_empty_cursor_means_the_first_page(client: TestClient, make_user: Any) -> None:
    """`?cursor=` is schema-valid (the spec only says "string"), so it must not be a 400."""
    h = auth(client, make_user)
    client.post(f"{API}/conversations", headers=h, json={"title": "only"})
    r = client.get(f"{API}/conversations", headers=h, params={"cursor": ""})
    assert r.status_code == 200 and [c["title"] for c in r.json()["items"]] == ["only"]


def test_create_rejects_a_json_null_body_but_accepts_no_body(
    client: TestClient, make_user: Any
) -> None:
    h = auth(client, make_user)
    null = client.post(
        f"{API}/conversations", headers={**h, "content-type": "application/json"}, content="null"
    )
    assert null.status_code == 422 and null.json()["error"]["code"] == "VALIDATION_FAILED"
    assert client.post(f"{API}/conversations", headers=h).json()["title"] == "New chat"


def test_has_active_run_and_pending_interrupt_are_reported(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    h = auth(client, make_user)
    me = user_id(db)
    interrupt = '{"interrupt_id": "i1", "tool": "web_search", "args": {"query": "x"}}'
    cid = seed(db, me, active_run_id=str(uuid4()), pending_interrupt=interrupt)
    item = client.get(f"{API}/conversations/{cid}", headers=h).json()
    assert item["has_active_run"] is True
    assert item["pending_interrupt"]["tool"] == "web_search"


# ───────────────────────────── get ─────────────────────────────


def test_get_returns_the_conversation(client: TestClient, make_user: Any) -> None:
    h = auth(client, make_user)
    created = client.post(f"{API}/conversations", headers=h, json={"title": "Plans"}).json()
    r = client.get(f"{API}/conversations/{created['id']}", headers=h)
    assert r.status_code == 200 and r.json() == created


@pytest.mark.parametrize(
    "cid", [str(uuid4()), "not-a-uuid", "123", "00000000-0000-0000-0000-00000000000"]
)
def test_get_unknown_or_malformed_id_is_404_never_422(
    client: TestClient, make_user: Any, cid: str
) -> None:
    h = auth(client, make_user)
    r = client.get(f"{API}/conversations/{cid}", headers=h)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


# ───────────────────────────── rename ─────────────────────────────


def test_rename_updates_the_title_and_trims(client: TestClient, make_user: Any) -> None:
    h = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=h).json()["id"]
    r = client.patch(f"{API}/conversations/{cid}", headers=h, json={"title": "  Q3 churn  "})
    assert r.status_code == 200 and r.json()["title"] == "Q3 churn"
    assert client.get(f"{API}/conversations/{cid}", headers=h).json()["title"] == "Q3 churn"


def test_rename_is_not_activity_and_does_not_reorder_the_list(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    """`updated_at` means last ACTIVITY (messages), so renaming an old chat must not float it up."""
    h = auth(client, make_user)
    me = user_id(db)
    old = seed(db, me, title="old", updated_at=TS)
    newer = seed(db, me, title="newer", updated_at=TS + timedelta(days=1))
    client.patch(f"{API}/conversations/{old}", headers=h, json={"title": "renamed"})
    ids = [c["id"] for c in client.get(f"{API}/conversations", headers=h).json()["items"]]
    assert ids == [newer, old]
    row = db.execute("SELECT updated_at FROM conversations WHERE id = %s", (old,)).fetchone()
    assert row and row[0] == TS


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 101},
        {"title": None},
        {"title": "a\x00b"},
    ],
)
def test_rename_validates(client: TestClient, make_user: Any, body: dict[str, Any]) -> None:
    h = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=h).json()["id"]
    r = client.patch(f"{API}/conversations/{cid}", headers=h, json=body)
    assert r.status_code == 422


def test_create_rejects_a_nul_character_in_the_title(client: TestClient, make_user: Any) -> None:
    """Postgres text cannot store NUL: a 422, never a 500 (found by the contract fuzz)."""
    h = auth(client, make_user)
    r = client.post(f"{API}/conversations", headers=h, json={"title": "a\x00b"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_rename_unknown_id_is_404(client: TestClient, make_user: Any) -> None:
    h = auth(client, make_user)
    r = client.patch(f"{API}/conversations/{uuid4()}", headers=h, json={"title": "x"})
    assert r.status_code == 404


# ───────────────────────────── delete ─────────────────────────────


def test_delete_removes_the_conversation(client: TestClient, make_user: Any) -> None:
    h = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=h).json()["id"]
    assert client.delete(f"{API}/conversations/{cid}", headers=h).status_code == 204
    assert client.get(f"{API}/conversations/{cid}", headers=h).status_code == 404
    assert client.delete(f"{API}/conversations/{cid}", headers=h).status_code == 404


def test_delete_removes_messages_and_langgraph_checkpoints(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    h = auth(client, make_user)
    me = user_id(db)
    cid = seed(db, me)
    other = seed(db, me, title="keep me")
    db.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content)"
        " VALUES (%s, %s, 'user', 'hi')",
        (cid, me),
    )
    for thread in (cid, other):  # LangGraph keys its tables by thread_id = conversation id
        db.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint)"
            " VALUES (%s, 'c1', '{}')",
            (thread,),
        )
        db.execute(
            "INSERT INTO checkpoint_blobs (thread_id, channel, version, type, blob)"
            " VALUES (%s, 'ch', '1', 'json', '\\x00')",
            (thread,),
        )
        db.execute(
            "INSERT INTO checkpoint_writes (thread_id, checkpoint_id, task_id, idx, channel, blob)"
            " VALUES (%s, 'c1', 't1', 0, 'ch', '\\x00')",
            (thread,),
        )

    assert client.delete(f"{API}/conversations/{cid}", headers=h).status_code == 204

    for table in ("messages", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        col = "conversation_id" if table == "messages" else "thread_id"
        gone = db.execute(
            f"SELECT count(*) FROM {table} WHERE {col} = %s",  # noqa: S608 - fixed names
            (cid,),
        ).fetchone()
        assert gone == (0,), f"{table} rows survived deletion"
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        kept = db.execute(
            f"SELECT count(*) FROM {table} WHERE thread_id = %s",  # noqa: S608 - fixed names
            (other,),
        ).fetchone()
        assert kept == (1,), f"{table}: other conversations' rows must be untouched"


# ───────────────────────────── auth ─────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/conversations", None),
        ("POST", "/conversations", {}),
        ("GET", f"/conversations/{uuid4()}", None),
        ("PATCH", f"/conversations/{uuid4()}", {"title": "x"}),
        ("DELETE", f"/conversations/{uuid4()}", None),
    ],
)
def test_every_conversation_endpoint_requires_authentication(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    r = client.request(method, f"{API}{path}", json=body)
    assert r.status_code == 401
