"""The documents API over HTTP against a real Postgres (TASK-042): list, read, delete, download.

Rows are written directly (the pipeline and the renderer have their own tests); the PDF files are
written under the test's `UPLOADS_DIR`, where the real renderer would put them.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from fastapi.testclient import TestClient

from .test_auth import API
from .test_conversations import auth, user_id

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
T0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)  # noqa: UP017 - matches the other tests


def make_document(
    db: psycopg.Connection[Any],
    settings: Settings,
    owner: str,
    *,
    title: str = "Launch plan",
    kind: str = "thread_summary",
    status: str = "ready",
    minutes: int = 0,
    error: str | None = None,
    conversation_id: str | None = None,
    write_file: bool = True,
) -> str:
    """Insert a document row (and, for `ready`, its PDF); returns the document id."""
    document_id = uuid.uuid4()
    relative = f"{owner}/documents/{document_id}.pdf" if status == "ready" else None
    if relative and write_file:
        target = settings.uploads_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PDF)
    db.execute(
        "INSERT INTO documents (id, user_id, conversation_id, title, kind, status, draft,"
        " storage_path, page_count, size_bytes, error, created_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,'{}'::jsonb,%s,%s,%s,%s,%s)",
        (
            document_id,
            owner,
            conversation_id,
            title,
            kind,
            status,
            relative,
            2 if status == "ready" else None,
            len(PDF) if status == "ready" else None,
            error,
            T0 + timedelta(minutes=minutes),
        ),
    )
    return str(document_id)


def stored_files(settings: Settings) -> list[str]:
    root = settings.uploads_dir
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.pdf")) if root.exists() else []


# ───────────────────────────── list ─────────────────────────────


def test_list_is_empty_for_a_new_user(client: TestClient, make_user: Any) -> None:
    headers = auth(client, make_user)
    r = client.get(f"{API}/documents", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None}


def test_list_is_newest_first_and_shows_the_spec_fields(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    owner = user_id(db)
    old = make_document(db, settings, owner, title="Old", minutes=0)
    new = make_document(db, settings, owner, title="New", kind="news_brief", minutes=5)
    pending = make_document(db, settings, owner, title="Soon", status="pending", minutes=9)

    body = client.get(f"{API}/documents", headers=headers).json()

    assert [d["id"] for d in body["items"]] == [pending, new, old]
    assert set(body["items"][1]) == {
        "id",
        "title",
        "kind",
        "status",
        "conversation_id",
        "message_id",
        "page_count",
        "size_bytes",
        "error",
        "created_at",
    }
    assert body["items"][1]["kind"] == "news_brief" and body["items"][1]["page_count"] == 2
    assert body["items"][0]["status"] == "pending" and body["items"][0]["size_bytes"] is None


def test_list_pages_with_a_cursor(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    owner = user_id(db)
    ids = [make_document(db, settings, owner, minutes=i) for i in range(5)]  # oldest first

    first = client.get(f"{API}/documents", headers=headers, params={"limit": 2}).json()
    assert [d["id"] for d in first["items"]] == [ids[4], ids[3]]
    assert first["next_cursor"]
    second = client.get(
        f"{API}/documents", headers=headers, params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [d["id"] for d in second["items"]] == [ids[2], ids[1]]
    last = client.get(
        f"{API}/documents", headers=headers, params={"limit": 2, "cursor": second["next_cursor"]}
    ).json()
    assert [d["id"] for d in last["items"]] == [ids[0]]
    assert last["next_cursor"] is None


def test_list_refuses_a_bad_cursor_and_a_bad_limit(client: TestClient, make_user: Any) -> None:
    headers = auth(client, make_user)
    bad_cursor = client.get(f"{API}/documents", headers=headers, params={"cursor": "nope"})
    assert bad_cursor.status_code == 400 and bad_cursor.json()["error"]["code"] == "BAD_REQUEST"
    for limit in (0, 101):
        r = client.get(f"{API}/documents", headers=headers, params={"limit": limit})
        assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_every_document_route_needs_a_token(client: TestClient) -> None:
    some = uuid.uuid4()
    for method, path in (
        ("GET", "/documents"),
        ("GET", f"/documents/{some}"),
        ("DELETE", f"/documents/{some}"),
        ("GET", f"/documents/{some}/download"),
    ):
        assert client.request(method, f"{API}{path}").status_code == 401


# ───────────────────────────── get ─────────────────────────────


def test_get_returns_one_document(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    owner = user_id(db)
    document_id = make_document(db, settings, owner, title="Q3 churn")
    r = client.get(f"{API}/documents/{document_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == document_id and r.json()["title"] == "Q3 churn"
    assert r.json()["status"] == "ready" and r.json()["error"] is None


def test_get_shows_why_a_render_failed(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    document_id = make_document(
        db, settings, user_id(db), status="failed", error="The document was too long to render."
    )
    r = client.get(f"{API}/documents/{document_id}", headers=headers).json()
    assert r["status"] == "failed" and r["error"] == "The document was too long to render."


def test_unknown_and_malformed_ids_are_the_same_404(client: TestClient, make_user: Any) -> None:
    headers = auth(client, make_user)
    bodies = []
    for ident in (str(uuid.uuid4()), "not-a-uuid"):
        for path in (f"/documents/{ident}", f"/documents/{ident}/download"):
            r = client.get(f"{API}{path}", headers=headers)
            assert r.status_code == 404
            bodies.append({k: v for k, v in r.json()["error"].items() if k != "request_id"})
        d = client.delete(f"{API}/documents/{ident}", headers=headers)
        assert d.status_code == 404
        bodies.append({k: v for k, v in d.json()["error"].items() if k != "request_id"})
    assert all(b == bodies[0] for b in bodies)


# ───────────────────────────── delete ─────────────────────────────


def test_delete_removes_the_row_and_the_pdf(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    owner = user_id(db)
    gone = make_document(db, settings, owner, title="Gone")
    kept = make_document(db, settings, owner, title="Kept")
    assert len(stored_files(settings)) == 2

    r = client.delete(f"{API}/documents/{gone}", headers=headers)

    assert r.status_code == 204 and r.content == b""
    assert client.get(f"{API}/documents/{gone}", headers=headers).status_code == 404
    assert [d["id"] for d in client.get(f"{API}/documents", headers=headers).json()["items"]] == [
        kept
    ]
    assert stored_files(settings) == [f"{owner}/documents/{kept}.pdf"]
    assert client.delete(f"{API}/documents/{gone}", headers=headers).status_code == 404


def test_delete_works_for_a_document_that_has_no_file_yet(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    pending = make_document(db, settings, user_id(db), status="pending")
    assert client.delete(f"{API}/documents/{pending}", headers=headers).status_code == 204
    assert db.execute("SELECT count(*) FROM documents").fetchone() == (0,)


def test_deleting_the_conversation_keeps_the_document(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    cid = client.post(f"{API}/conversations", headers=headers, json={}).json()["id"]
    document_id = make_document(db, settings, user_id(db), conversation_id=cid)

    assert client.delete(f"{API}/conversations/{cid}", headers=headers).status_code == 204

    r = client.get(f"{API}/documents/{document_id}", headers=headers)
    assert r.status_code == 200 and r.json()["conversation_id"] is None
    assert client.get(f"{API}/documents/{document_id}/download", headers=headers).status_code == 200


# ───────────────────────────── download ─────────────────────────────


def test_download_sends_the_pdf_as_an_attachment(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    document_id = make_document(db, settings, user_id(db), title="Q3 churn analysis")

    r = client.get(f"{API}/documents/{document_id}/download", headers=headers)

    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith(
        'attachment; filename="Q3 churn analysis.pdf"'
    )
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("title", "ascii_name"),
    [
        ('evil"; filename="x.exe', "evil filename x.exe"),
        ("line\r\nSet-Cookie: a=b", "line Set-Cookie a b"),
        ("../../etc/passwd", "etc passwd"),
        ("plan..final", "plan final"),
        ("Résumé de réunion", "Resume de reunion"),
        ("日本語のタイトル", "document"),
        ("   ", "document"),
        ("a" * 300, "a" * 80),
    ],
)
def test_download_file_name_cannot_carry_header_tricks(
    client: TestClient,
    make_user: Any,
    db: psycopg.Connection[Any],
    settings: Settings,
    title: str,
    ascii_name: str,
) -> None:
    headers = auth(client, make_user)
    document_id = make_document(db, settings, user_id(db), title=title)

    r = client.get(f"{API}/documents/{document_id}/download", headers=headers)

    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert disposition.startswith(f'attachment; filename="{ascii_name}.pdf"; filename*=UTF-8')
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('"') == 2  # the title could not add a quote of its own
    assert disposition.count(";") == 2  # ... or a parameter of its own


def test_download_is_a_409_until_the_pdf_exists(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    owner = user_id(db)
    pending = make_document(db, settings, owner, status="pending")
    failed = make_document(db, settings, owner, status="failed", error="x")

    for document_id in (pending, failed):
        r = client.get(f"{API}/documents/{document_id}/download", headers=headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_NOT_READY"
    assert (
        client.get(f"{API}/documents/{pending}/download", headers=headers).json()["error"][
            "message"
        ]
        != client.get(f"{API}/documents/{failed}/download", headers=headers).json()["error"][
            "message"
        ]
    )


def test_download_of_a_missing_file_is_a_404_not_a_500(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], settings: Settings
) -> None:
    headers = auth(client, make_user)
    document_id = make_document(db, settings, user_id(db), write_file=False)
    r = client.get(f"{API}/documents/{document_id}/download", headers=headers)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_a_stored_path_that_leaves_the_uploads_directory_is_never_served(
    client: TestClient,
    make_user: Any,
    db: psycopg.Connection[Any],
    settings: Settings,
    tmp_path: Any,
) -> None:
    headers = auth(client, make_user)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-secret")
    document_id = make_document(db, settings, user_id(db), write_file=False)
    db.execute(
        "UPDATE documents SET storage_path = %s WHERE id = %s", (f"../{secret.name}", document_id)
    )

    r = client.get(f"{API}/documents/{document_id}/download", headers=headers)

    assert r.status_code == 404
    assert b"secret" not in r.content
