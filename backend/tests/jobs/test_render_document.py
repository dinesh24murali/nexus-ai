"""`render_document` and `cleanup_documents` against a real Postgres and the real renderer
(TASK-041)."""

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pypdf
import pytest
import structlog.testing
from app.core.config import Settings
from app.documents import render, storage
from app.documents.draft import DocumentDraft, Section, Source
from app.jobs import handlers, periodic, queue
from app.jobs.handlers import JobContext
from app.jobs.queue import ClaimedJob
from app.services.documents import DocumentsService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def draft(
    body: str = "Churn fell to **3.1%** [1].", *, title: str = "Quarterly review"
) -> DocumentDraft:
    return DocumentDraft(
        title=title,
        sections=[Section(heading="Findings", body_md=body)],
        sources=[Source(index=1, title="Q3 report", url="https://example.com/q3")],
    )


@pytest.fixture
def user(make_user: Any) -> str:
    return str(make_user())


@pytest.fixture
def context(session_factory: Factory, settings: Settings) -> JobContext:
    return JobContext(factory=session_factory, settings=settings)


async def make(
    session_factory: Factory, settings: Settings, user: str, body: DocumentDraft | None = None
) -> uuid.UUID:
    ref = await DocumentsService(session_factory, settings).create(
        uuid.UUID(user),
        conversation_id=None,
        message_id=None,
        kind="thread_summary",
        draft=body or draft(),
    )
    assert ref
    return ref.id


async def render_next(context: JobContext, *, attempts: int | None = None) -> ClaimedJob:
    job = await queue.claim(context.factory, "w1")
    assert job is not None and job.kind == "render_document"
    if attempts is not None:
        job = ClaimedJob(job.id, job.kind, job.payload, attempts, job.max_attempts, job.user_id)
    await handlers.JOBS["render_document"].handler(context, job)
    return job


def row(db: psycopg.Connection[Any], doc: uuid.UUID) -> dict[str, Any]:
    cur = db.execute(
        "SELECT status, storage_path, page_count, size_bytes, error FROM documents WHERE id = %s",
        (doc,),
    )
    values = cur.fetchone()
    assert values
    return dict(
        zip(("status", "storage_path", "page_count", "size_bytes", "error"), values, strict=True)
    )


def text_of(path: Path) -> str:
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(str(path)).pages)


async def test_a_job_without_a_user_is_refused(context: JobContext) -> None:
    job = ClaimedJob(1, "render_document", {"document_id": str(uuid.uuid4())}, 1, 2, None)
    with pytest.raises(ValueError, match="needs a user"):
        await handlers.JOBS["render_document"].handler(context, job)


async def test_a_stored_draft_that_is_not_a_draft_fails_the_document_for_good(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
) -> None:
    doc = await make(session_factory, settings, user)
    db.execute("UPDATE documents SET draft = '{\"nonsense\": 1}'::jsonb WHERE id = %s", (doc,))

    await render_next(context)

    assert row(db, doc)["status"] == "failed" and row(db, doc)["error"] == render.REASON_FAILED


async def test_a_refused_request_in_the_renderer_is_logged_as_a_count(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = render.render_pdf

    async def denying(html: str, out: Path, **kw: Any) -> Any:
        result = await real(html, out, **kw)
        return render.Rendered(result.pages, result.size_bytes, denied=2)

    monkeypatch.setattr("app.jobs.handlers.render_pdf", denying)
    await make(session_factory, settings, user)

    with structlog.testing.capture_logs() as logs:
        await render_next(context)

    (warning,) = [e for e in logs if e["event"] == "render_denied_fetches"]
    assert warning["count"] == 2 and "url" not in warning


def test_the_job_is_registered_with_the_designed_limits() -> None:
    spec = handlers.JOBS["render_document"]
    assert (spec.timeout_s, spec.max_attempts) == (60, 2)
    assert spec.timeout_s > render.RENDER_TIMEOUT_S  # the child is killed before the job is


async def test_a_pending_document_becomes_a_ready_pdf_under_the_users_directory(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
) -> None:
    doc = await make(session_factory, settings, user)

    await render_next(context)

    values = row(db, doc)
    assert values["status"] == "ready" and values["error"] is None
    assert values["storage_path"] == f"{user}/documents/{doc}.pdf"
    pdf = settings.uploads_dir / values["storage_path"]
    assert values["size_bytes"] == pdf.stat().st_size and values["page_count"] == 1
    text = text_of(pdf)
    assert "Quarterly review" in text and "Churn fell to 3.1% [1]." in text
    assert "https://example.com/q3" in text
    assert not pdf.with_name(pdf.name + ".part").exists()


async def test_running_it_twice_changes_nothing(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
) -> None:
    doc = await make(session_factory, settings, user)
    await render_next(context)
    first = (settings.uploads_dir / row(db, doc)["storage_path"]).read_bytes()

    job = ClaimedJob(99, "render_document", {"document_id": str(doc)}, 1, 2, uuid.UUID(user))
    await handlers.JOBS["render_document"].handler(context, job)

    assert (settings.uploads_dir / row(db, doc)["storage_path"]).read_bytes() == first


async def test_a_ready_document_whose_file_is_gone_is_rendered_again(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
) -> None:
    doc = await make(session_factory, settings, user)
    await render_next(context)
    (settings.uploads_dir / row(db, doc)["storage_path"]).unlink()

    job = ClaimedJob(99, "render_document", {"document_id": str(doc)}, 1, 2, uuid.UUID(user))
    await handlers.JOBS["render_document"].handler(context, job)

    assert (settings.uploads_dir / row(db, doc)["storage_path"]).is_file()


async def test_a_document_over_the_page_limit_fails_for_good_with_a_reason(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.jobs.handlers.render_pdf", _with(max_pages=1))
    big = DocumentDraft(
        title="Long",
        sections=[Section(heading=f"S{i}", body_md="x\n\n" * 60) for i in range(12)],
        sources=[],
    )
    doc = await make(session_factory, settings, user, big)

    await render_next(context)  # returns normally: not a retry

    values = row(db, doc)
    assert values["status"] == "failed" and values["error"] == render.REASON_TOO_LONG
    assert values["storage_path"] is None
    assert (
        not list((settings.uploads_dir / user).rglob("*.pdf*"))
        if (settings.uploads_dir / user).exists()
        else True
    )


async def test_a_render_that_takes_too_long_is_a_failed_document(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.jobs.handlers.render_pdf", _with(timeout_s=0.01))
    doc = await make(session_factory, settings, user)

    await render_next(context)

    assert row(db, doc)["status"] == "failed" and row(db, doc)["error"] == render.REASON_TIMEOUT


async def test_an_unexpected_error_is_retried_and_only_the_last_attempt_fails_the_document(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk exploded at /secret/path")

    monkeypatch.setattr("app.jobs.handlers.render_pdf", broken)
    doc = await make(session_factory, settings, user)
    job = await queue.claim(session_factory, "w1")
    assert job

    with pytest.raises(OSError, match="disk exploded"):
        await handlers.JOBS["render_document"].handler(
            context, ClaimedJob(job.id, job.kind, job.payload, 1, 2, job.user_id)
        )
    assert row(db, doc)["status"] == "pending"  # the queue will retry

    with pytest.raises(OSError, match="disk exploded"):
        await handlers.JOBS["render_document"].handler(
            context, ClaimedJob(job.id, job.kind, job.payload, 2, 2, job.user_id)
        )
    values = row(db, doc)
    assert values["status"] == "failed" and values["error"] == render.REASON_FAILED
    assert "secret" not in (values["error"] or "")


async def test_a_document_deleted_while_it_renders_leaves_no_file(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = render.render_pdf

    async def delete_then_render(html: str, out: Path, **kw: Any) -> Any:
        result = await real(html, out, **kw)
        db.execute("DELETE FROM documents")  # the user deleted it meanwhile
        return result

    monkeypatch.setattr("app.jobs.handlers.render_pdf", delete_then_render)
    await make(session_factory, settings, user)

    await render_next(context)

    assert list((settings.uploads_dir / user / "documents").glob("*")) == []


async def test_a_job_for_a_deleted_or_foreign_document_does_nothing(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    make_user: Any,
) -> None:
    other = str(make_user("other@example.com"))
    doc = await make(session_factory, settings, other)

    # A job that names someone else's document under MY user id finds nothing.
    job = ClaimedJob(1, "render_document", {"document_id": str(doc)}, 1, 2, uuid.UUID(user))
    await handlers.JOBS["render_document"].handler(context, job)

    assert row(db, doc)["status"] == "pending"
    assert not (settings.uploads_dir / user).exists()


async def test_hostile_content_renders_with_no_request_and_no_active_link(
    context: JobContext,
    session_factory: Factory,
    settings: Settings,
    db: psycopg.Connection[Any],
    user: str,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("CANARY-file-contents", encoding="utf-8")
    body = f'![x](file://{secret}) <img src="file://{secret}"> [js](javascript:alert(1)) ![m](http://169.254.169.254/x)'
    doc = await make(session_factory, settings, user, draft(body))

    with structlog.testing.capture_logs() as logs:
        await render_next(context)

    values = row(db, doc)
    assert values["status"] == "ready"
    assert "CANARY-file-contents" not in text_of(settings.uploads_dir / values["storage_path"])
    # The sanitiser left nothing to load, so the renderer never even asked.
    assert not [e for e in logs if e["event"] == "render_denied_fetches"]


# ---------- cleanup_documents


async def test_cleanup_keeps_each_users_newest_and_removes_rows_then_files(
    session_factory: Factory, settings: Settings, db: psycopg.Connection[Any], make_user: Any
) -> None:
    keep = 3
    s = settings.model_copy(update={"documents_keep": keep})
    users = [str(make_user("a@example.com")), str(make_user("b@example.com"))]
    ids: dict[str, list[uuid.UUID]] = {}
    for u in users:
        ids[u] = []
        for n in range(keep + 2):
            doc = uuid.uuid4()
            rel = storage.relative_path(uuid.UUID(u), doc)
            (s.uploads_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (s.uploads_dir / rel).write_bytes(b"%PDF-")
            db.execute(
                "INSERT INTO documents"
                " (id, user_id, title, kind, status, draft, storage_path, created_at)"
                " VALUES (%s,%s,'t','answer','ready','{}'::jsonb,%s,"
                " '2026-11-01T00:00:00Z'::timestamptz + make_interval(mins => %s))",
                (doc, u, rel, n),
            )
            ids[u].append(doc)

    removed = await periodic.cleanup_documents(session_factory, s)

    assert removed == 4  # 2 per user
    for u in users:
        kept = {
            r[0] for r in db.execute("SELECT id FROM documents WHERE user_id = %s", (u,)).fetchall()
        }
        assert kept == set(ids[u][-keep:])  # the newest
        for doc in ids[u][:-keep]:
            assert not (s.uploads_dir / storage.relative_path(uuid.UUID(u), doc)).exists()
        for doc in ids[u][-keep:]:
            assert (s.uploads_dir / storage.relative_path(uuid.UUID(u), doc)).exists()
    assert await periodic.cleanup_documents(session_factory, s) == 0  # idempotent


async def test_cleanup_never_deletes_a_file_outside_the_uploads_directory(
    settings: Settings, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    assert storage.remove(settings.uploads_dir, "../outside.txt") is False
    assert storage.remove(settings.uploads_dir, "") is False
    assert storage.remove(settings.uploads_dir, None) is False
    assert storage.remove(settings.uploads_dir, ".") is False
    assert outside.read_text() == "keep me"


def _with(**fixed: Any) -> Any:
    real = render.render_pdf

    async def render_with(html: str, out: Path, **kw: Any) -> Any:
        return await real(html, out, **{**kw, **fixed})

    return render_with
