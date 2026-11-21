"""`DELETE /files/{id}` over HTTP against real Postgres and Qdrant (TASK-020)."""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from tests.conftest import QDRANT_URL

from .test_auth import API
from .test_conversations import auth
from .test_files import PDF, TXT, disk_files, upload

pytestmark = pytest.mark.integration


@pytest.fixture
def alias() -> Iterator[str]:
    """The app's own Qdrant alias for this test, removed afterwards."""
    name = f"test_documents_{uuid.uuid4().hex[:12]}"
    yield name

    async def drop() -> None:
        client = AsyncQdrantClient(url=QDRANT_URL)
        try:
            if await client.collection_exists(f"{name}_v1"):
                await client.delete_collection(f"{name}_v1")
        finally:
            await client.close()

    asyncio.run(drop())


@pytest.fixture
def app_client(make_client: Callable[..., TestClient], alias: str) -> TestClient:
    return make_client(qdrant_url=QDRANT_URL, qdrant_collection=alias)


def delete(client: TestClient, headers: dict[str, str], file_id: str) -> Any:
    return client.delete(f"{API}/files/{file_id}", headers=headers)


def test_deleting_a_file_returns_204_and_removes_it_everywhere(
    app_client: TestClient, make_user: Any, settings: Settings, db: psycopg.Connection[Any]
) -> None:
    headers = auth(app_client, make_user)
    file_id = upload(app_client, headers).json()["id"]
    assert len(disk_files(settings)) == 1

    r = delete(app_client, headers, file_id)

    assert r.status_code == 204
    assert r.content == b""
    assert app_client.get(f"{API}/files/{file_id}", headers=headers).status_code == 404
    assert app_client.get(f"{API}/files", headers=headers).json()["items"] == []
    assert disk_files(settings) == []
    assert db.execute("SELECT status FROM jobs WHERE kind = 'ingest_file'").fetchall() == [
        ("cancelled",)
    ]


def test_the_same_content_can_be_uploaded_again_after_deleting(
    app_client: TestClient, make_user: Any
) -> None:
    headers = auth(app_client, make_user)
    first = upload(app_client, headers).json()["id"]
    assert upload(app_client, headers).status_code == 409  # the duplicate check

    assert delete(app_client, headers, first).status_code == 204

    second = upload(app_client, headers)
    assert second.status_code == 202
    assert second.json()["id"] != first


def test_a_second_delete_unknown_and_malformed_ids_are_the_same_404(
    app_client: TestClient, make_user: Any
) -> None:
    headers = auth(app_client, make_user)
    file_id = upload(app_client, headers).json()["id"]
    assert delete(app_client, headers, file_id).status_code == 204

    bodies = []
    for missing in (file_id, str(uuid.uuid4()), "not-a-uuid"):
        r = delete(app_client, headers, missing)
        assert r.status_code == 404
        bodies.append({k: v for k, v in r.json()["error"].items() if k != "request_id"})
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0]["code"] == "NOT_FOUND"


def test_deleting_needs_a_session(app_client: TestClient) -> None:
    r = app_client.delete(f"{API}/files/{uuid.uuid4()}")

    assert r.status_code == 401


def test_another_users_file_cannot_be_deleted(
    app_client: TestClient, make_user: Any, settings: Settings
) -> None:
    alice = auth(app_client, make_user, email="alice@example.com")
    bob = auth(app_client, make_user, email="bob@example.com")
    alices = upload(app_client, alice, PDF, "alice.pdf").json()["id"]
    bobs = upload(app_client, bob, TXT, "bob.txt").json()["id"]

    r = delete(app_client, bob, alices)

    assert r.status_code == 404 and "alice" not in r.text.lower()
    assert [f["id"] for f in app_client.get(f"{API}/files", headers=alice).json()["items"]] == [
        alices
    ]
    assert len(disk_files(settings)) == 2
    assert delete(app_client, bob, bobs).status_code == 204  # his own still works
