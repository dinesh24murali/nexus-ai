"""Generated PDF documents: list, read, delete, download. The spec is the contract (TASK-042).

Documents are made by the `generate_document` pipeline and rendered by the worker; this router only
reads and removes them. Every call is scoped to the caller: another user's document is a 404.
"""

import re
import unicodedata
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Response
from starlette.responses import FileResponse

from app.api.deps import CurrentUserDep, DocumentsDep, FactoryDep
from app.api.schemas import DocumentOut, DocumentPage
from app.core.errors import ApiError
from app.db.repositories import documents as repo

router = APIRouter(prefix="/documents", tags=["documents"])

_FALLBACK_NAME = "document"
_MAX_NAME = 80


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Document not found.")


def _document_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise _not_found() from None


def _ascii_name(title: str) -> str:
    """A file name safe for a header: ASCII letters, digits, space, dot, dash and underscore only
    (no quotes, semicolons, slashes or control characters), at most 80 characters."""
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    plain = re.sub(r"[^A-Za-z0-9 ._-]+", " ", plain)
    plain = re.sub(r"\.{2,}", " ", plain)  # no `..` in a name, even without a slash beside it
    plain = re.sub(r"\s+", " ", plain).strip(" .-_")[:_MAX_NAME].strip(" .-_")
    return plain or _FALLBACK_NAME


def content_disposition(title: str) -> str:
    """`attachment` with a safe ASCII `filename` and the real title as RFC 5987 `filename*`."""
    utf8 = quote(f"{title.strip()[:_MAX_NAME]}.pdf" if title.strip() else f"{_FALLBACK_NAME}.pdf")
    return f"attachment; filename=\"{_ascii_name(title)}.pdf\"; filename*=UTF-8''{utf8}"


@router.get("", response_model=DocumentPage, summary="List generated documents, newest first")
async def list_documents(
    current: CurrentUserDep,
    factory: FactoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> DocumentPage:
    try:
        decoded = repo.decode_cursor(cursor) if cursor else None
    except repo.InvalidCursorError:
        raise ApiError(400, "BAD_REQUEST", "Invalid cursor.") from None
    async with factory() as session:
        items, next_cursor = await repo.list_page(session, current.id, limit=limit, cursor=decoded)
    return DocumentPage(items=[DocumentOut.from_model(d) for d in items], next_cursor=next_cursor)


@router.get(
    "/{document_id}", response_model=DocumentOut, summary="Get document metadata and status"
)
async def get_document(
    document_id: str, current: CurrentUserDep, factory: FactoryDep
) -> DocumentOut:
    async with factory() as session:
        row = await repo.get(session, current.id, _document_id(document_id))
    if row is None:
        raise _not_found()
    return DocumentOut.from_model(row)


@router.delete(
    "/{document_id}",
    status_code=204,
    summary="Delete a document and its PDF",
    response_class=Response,
)
async def delete_document(
    document_id: str, current: CurrentUserDep, documents: DocumentsDep
) -> Response:
    await documents.delete(current.id, _document_id(document_id))
    return Response(status_code=204)


@router.get(
    "/{document_id}/download",
    summary="Download the PDF",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}}, "description": "The PDF file"}},
)
async def download_document(
    document_id: str, current: CurrentUserDep, documents: DocumentsDep
) -> FileResponse:
    row, path = await documents.pdf_for_download(current.id, _document_id(document_id))
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(row.title),
            # The PDF is private and was made from the user's own conversation: never cached by a
            # shared proxy; nosniff so a browser never treats the bytes as anything but a PDF.
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
