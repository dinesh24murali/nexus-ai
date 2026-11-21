"""Uploading and listing files over HTTP against a real Postgres (TASK-017, FS-004)."""

import hashlib
import io
import zipfile
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

from tests.rag.builders import docx_bytes, pdf_bytes

from .test_auth import API
from .test_conversations import auth, user_id

pytestmark = pytest.mark.integration

PDF = pdf_bytes(["The quarterly report shows revenue growth in every region this year."])
TXT = b"Plain notes about the project.\n"


def upload(
    client: TestClient,
    headers: dict[str, str],
    data: bytes = PDF,
    name: str | None = "report.pdf",
    *,
    field: str = "file",
) -> Any:
    files = {field: (name or "", io.BytesIO(data), "application/octet-stream")}
    return client.post(f"{API}/files", headers=headers, files=files)


@pytest.fixture
def make_client(settings: Settings, db_url: str) -> Generator[Callable[..., TestClient]]:
    """A client with settings overrides (small limits make the limit tests cheap)."""
    clients: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        app = create_app(settings.model_copy(update={"database_url": db_url, **overrides}))
        c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def signed_in(client: TestClient, make_user: Any) -> tuple[TestClient, dict[str, str]]:
    return client, auth(client, make_user)


def disk_files(settings: Settings) -> list[Path]:
    root = settings.uploads_dir
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def jobs(db: psycopg.Connection[Any]) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT kind, payload, idempotency_key, user_id, max_attempts, status FROM jobs"
        " WHERE kind = 'ingest_file'"
    ).fetchall()


# ───────────── happy path ─────────────


def test_an_upload_is_accepted_stored_and_queued(
    signed_in: tuple[TestClient, dict[str, str]],
    settings: Settings,
    db: psycopg.Connection[Any],
) -> None:
    client, headers = signed_in

    r = upload(client, headers)

    assert r.status_code == 202
    body = r.json()
    assert set(body) == {
        "id", "filename", "mime_type", "size_bytes", "status", "error", "page_count",
        "chunk_count", "created_at",
    }  # fmt: skip
    assert (body["filename"], body["mime_type"], body["size_bytes"]) == (
        "report.pdf",
        "application/pdf",
        len(PDF),
    )
    assert (body["status"], body["error"], body["page_count"], body["chunk_count"]) == (
        "pending",
        None,
        None,
        None,
    )

    uid = user_id(db)
    stored = disk_files(settings)
    assert [p.relative_to(settings.uploads_dir).as_posix() for p in stored] == [
        f"{uid}/{body['id']}"
    ]
    assert stored[0].read_bytes() == PDF

    sha = hashlib.sha256(PDF).hexdigest()
    row = db.execute(
        "SELECT sha256, storage_path FROM files WHERE id = %s", (body["id"],)
    ).fetchone()
    assert row == (sha, f"{uid}/{body['id']}")
    assert jobs(db) == [
        (
            "ingest_file",
            {"file_id": body["id"]},
            f"ingest:{body['id']}:{sha}",
            __import__("uuid").UUID(uid),
            3,
            "queued",
        )
    ]


@pytest.mark.parametrize(
    ("name", "data", "mime"),
    [
        ("notes.txt", TXT, "text/plain"),
        ("README.md", b"# Title\n\nBody\n", "text/markdown"),
        (
            "memo.docx",
            docx_bytes(["Hello"]),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("UPPER.PDF", PDF, "application/pdf"),
    ],
)
def test_every_supported_type_is_accepted(
    signed_in: tuple[TestClient, dict[str, str]], name: str, data: bytes, mime: str
) -> None:
    client, headers = signed_in

    r = upload(client, headers, data, name)

    assert r.status_code == 202, r.text
    assert r.json()["mime_type"] == mime


def test_the_file_name_is_cleaned_and_never_used_on_disk(
    signed_in: tuple[TestClient, dict[str, str]], settings: Settings
) -> None:
    client, headers = signed_in

    r = upload(client, headers, TXT, "../../etc/passwd.txt")

    assert r.status_code == 202
    assert r.json()["filename"] == "passwd.txt"
    assert all(p.is_relative_to(settings.uploads_dir) for p in disk_files(settings))
    assert [p.name for p in disk_files(settings)] == [r.json()["id"]]


def test_a_windows_path_keeps_only_the_base_name(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = signed_in

    r = upload(client, headers, TXT, "C:\\Users\\me\\notes.txt")

    assert r.json()["filename"] == "notes.txt"


# ───────────── validation errors (FS-004) ─────────────


def error_code(r: Any) -> str:
    return str(r.json()["error"]["code"])


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("tool.exe", b"MZ\x90\x00"),
        ("noextension", TXT),
        (".txt", TXT),
        ("report.doc", b"\xd0\xcf\x11\xe0"),
        ("a.pdf.exe", PDF),
        ("fake.pdf", TXT),  # text that claims to be a PDF
        ("fake.txt", PDF),  # a PDF that claims to be text
        ("fake.md", docx_bytes(["x"])),  # a DOCX that claims to be markdown
        ("blob.txt", b"abc\x00\x01\x02def" * 50),  # binary content under a text name
        ("fake.docx", TXT),
        ("fake.docx", b"PK\x03\x04 not really a zip"),
    ],
)
def test_unsupported_types_are_415_and_leave_nothing_behind(
    signed_in: tuple[TestClient, dict[str, str]],
    settings: Settings,
    db: psycopg.Connection[Any],
    name: str,
    data: bytes,
) -> None:
    client, headers = signed_in

    r = upload(client, headers, data, name)

    assert r.status_code == 415, r.text
    assert error_code(r) == "FILE_TYPE_UNSUPPORTED"
    assert disk_files(settings) == []
    assert db.execute("SELECT count(*) FROM files").fetchone() == (0,)


def test_a_zip_that_is_not_a_word_document_is_refused(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = signed_in
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("hello.txt", "hi")

    r = upload(client, headers, out.getvalue(), "archive.docx")

    assert r.status_code == 415 and error_code(r) == "FILE_TYPE_UNSUPPORTED"


def test_a_file_over_the_limit_is_413_and_leaves_nothing_behind(
    make_client: Callable[..., TestClient],
    make_user: Any,
    settings: Settings,
    db: psycopg.Connection[Any],
) -> None:
    client = make_client(max_upload_bytes=100)
    headers = auth(client, make_user)

    r = upload(client, headers, b"a" * 101, "big.txt")

    assert r.status_code == 413
    assert error_code(r) == "FILE_TOO_LARGE"
    assert disk_files(settings) == []
    assert db.execute("SELECT count(*) FROM files").fetchone() == (0,)


def test_a_file_exactly_at_the_limit_is_accepted(
    make_client: Callable[..., TestClient], make_user: Any
) -> None:
    client = make_client(max_upload_bytes=100)
    headers = auth(client, make_user)

    assert upload(client, headers, b"a" * 100, "edge.txt").status_code == 202


def test_an_oversize_content_length_is_refused_before_the_body_is_read(
    make_client: Callable[..., TestClient], make_user: Any, settings: Settings
) -> None:
    client = make_client(max_upload_bytes=100)
    headers = auth(client, make_user)

    r = client.post(
        f"{API}/files",
        headers={**headers, "content-type": "multipart/form-data; boundary=x"},
        content=b"x" * (1024 * 1024 + 500),
    )

    assert r.status_code == 413 and error_code(r) == "FILE_TOO_LARGE"
    assert disk_files(settings) == []


def test_the_same_content_twice_is_409_even_under_another_name(
    signed_in: tuple[TestClient, dict[str, str]],
    settings: Settings,
    db: psycopg.Connection[Any],
) -> None:
    client, headers = signed_in
    first = upload(client, headers, PDF, "one.pdf").json()

    r = upload(client, headers, PDF, "two.pdf")

    assert r.status_code == 409
    assert error_code(r) == "FILE_DUPLICATE"
    assert r.json()["error"]["details"] == {"file_id": first["id"]}
    assert len(disk_files(settings)) == 1
    assert len(jobs(db)) == 1


def test_the_same_content_from_another_user_is_fine(
    client: TestClient, make_user: Any, settings: Settings
) -> None:
    a = auth(client, make_user, "a@example.com")
    b = auth(client, make_user, "b@example.com")

    assert upload(client, a).status_code == 202
    assert upload(client, b).status_code == 202
    assert len(disk_files(settings)) == 2


def test_the_per_user_limit_is_409_and_leaves_nothing_behind(
    make_client: Callable[..., TestClient],
    make_user: Any,
    settings: Settings,
    db: psycopg.Connection[Any],
) -> None:
    client = make_client(max_files_per_user=2)
    headers = auth(client, make_user)
    assert upload(client, headers, b"one", "1.txt").status_code == 202
    assert upload(client, headers, b"two", "2.txt").status_code == 202

    r = upload(client, headers, b"three", "3.txt")

    assert r.status_code == 409
    assert error_code(r) == "FILE_LIMIT_REACHED"
    assert len(disk_files(settings)) == 2
    assert len(jobs(db)) == 2


def test_the_limit_holds_under_concurrent_uploads(
    make_client: Callable[..., TestClient], make_user: Any, db: psycopg.Connection[Any]
) -> None:
    client = make_client(max_files_per_user=2)
    headers = auth(client, make_user)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda i: upload(client, headers, f"content {i}".encode(), f"{i}.txt").status_code,
                range(6),
            )
        )

    assert sorted(results) == [202, 202, 409, 409, 409, 409]
    assert db.execute("SELECT count(*) FROM files").fetchone() == (2,)


def test_the_limit_counts_only_the_users_own_files(
    make_client: Callable[..., TestClient], make_user: Any
) -> None:
    client = make_client(max_files_per_user=1)
    a = auth(client, make_user, "a@example.com")
    b = auth(client, make_user, "b@example.com")

    assert upload(client, a, b"one", "1.txt").status_code == 202
    assert upload(client, b, b"two", "2.txt").status_code == 202


@pytest.mark.parametrize("what", ["no_part", "wrong_field", "empty_file", "not_multipart"])
def test_a_malformed_request_is_422(
    signed_in: tuple[TestClient, dict[str, str]], what: str
) -> None:
    client, headers = signed_in

    if what == "no_part":
        r = client.post(f"{API}/files", headers=headers, files={"other": ("a", b"x")})
    elif what == "wrong_field":
        r = upload(client, headers, TXT, "a.txt", field="document")
    elif what == "empty_file":
        r = upload(client, headers, b"", "empty.txt")
    else:
        r = client.post(f"{API}/files", headers=headers, json={"file": "x"})

    assert r.status_code == 422, r.text
    assert error_code(r) == "VALIDATION_FAILED"


def test_the_file_field_must_be_a_file_not_text(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = signed_in

    r = client.post(f"{API}/files", headers=headers, data={"file": "just text"})

    assert r.status_code == 422 and error_code(r) == "VALIDATION_FAILED"


def test_uploading_needs_a_signed_in_user(client: TestClient, settings: Settings) -> None:
    r = upload(client, {})

    assert r.status_code == 401
    assert disk_files(settings) == []


def test_an_idempotency_key_header_is_accepted(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = signed_in

    r = upload(client, {**headers, "idempotency-key": str(uuid4())})

    assert r.status_code == 202


# ───────────── reading ─────────────


def test_files_are_listed_newest_first_with_pages(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = signed_in
    ids = [upload(client, headers, f"n{i}".encode(), f"{i}.txt").json()["id"] for i in range(5)]

    first = client.get(f"{API}/files?limit=2", headers=headers).json()
    second = client.get(
        f"{API}/files?limit=2&cursor={first['next_cursor']}", headers=headers
    ).json()
    third = client.get(
        f"{API}/files?limit=2&cursor={second['next_cursor']}", headers=headers
    ).json()

    assert [f["id"] for f in first["items"] + second["items"] + third["items"]] == ids[::-1]
    assert third["next_cursor"] is None


def test_a_bad_cursor_is_400(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = signed_in

    r = client.get(f"{API}/files?cursor=garbage", headers=headers)

    assert r.status_code == 400 and error_code(r) == "BAD_REQUEST"


def test_an_empty_list_is_an_empty_page(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = signed_in

    assert client.get(f"{API}/files", headers=headers).json() == {"items": [], "next_cursor": None}


def test_a_file_can_be_read_by_its_owner(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = signed_in
    created = upload(client, headers).json()

    r = client.get(f"{API}/files/{created['id']}", headers=headers)

    assert r.status_code == 200
    assert r.json() == created


def test_another_users_file_is_404_everywhere(client: TestClient, make_user: Any) -> None:
    owner = auth(client, make_user, "a@example.com")
    other = auth(client, make_user, "b@example.com")
    created = upload(client, owner).json()

    assert client.get(f"{API}/files/{created['id']}", headers=other).status_code == 404
    assert client.get(f"{API}/files", headers=other).json()["items"] == []
    assert client.get(f"{API}/files", headers=owner).json()["items"][0]["id"] == created["id"]


@pytest.mark.parametrize("raw", ["not-a-uuid", str(uuid4())])
def test_an_unknown_or_malformed_id_is_404(
    signed_in: tuple[TestClient, dict[str, str]], raw: str
) -> None:
    client, headers = signed_in

    r = client.get(f"{API}/files/{raw}", headers=headers)

    assert r.status_code == 404 and error_code(r) == "NOT_FOUND"
