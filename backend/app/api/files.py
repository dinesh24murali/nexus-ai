"""Files: upload, list, read. Deletion arrives with TASK-020. The spec is the contract."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import UploadFile

from app.api.deps import CurrentUserDep, FactoryDep, FilesDep
from app.api.schemas import FileOut, FilePage
from app.core.errors import ApiError
from app.db.repositories import files as repo

router = APIRouter(prefix="/files", tags=["files"])

_MULTIPART_OVERHEAD = 1024 * 1024  # boundaries and part headers around a file of the maximum size


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "File not found.")


def _bad_body(message: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", "file"), "msg": message}]
    )


@router.post(
    "",
    status_code=202,
    response_model=FileOut,
    summary="Upload a file; processing happens asynchronously",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {"$ref": "#/components/schemas/UploadFileRequest"}
                }
            },
        }
    },
)
async def upload_file(request: Request, current: CurrentUserDep, files: FilesDep) -> FileOut:
    # The body is read by hand, not through a `File(...)` parameter: FastAPI would parse the whole
    # multipart body before any of our code ran, so an oversize upload could only be refused after
    # it had been received. An honest Content-Length is refused up front.
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > files.max_bytes + _MULTIPART_OVERHEAD:
        raise files.too_large()
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise _bad_body("Send the file as multipart/form-data in the field 'file'.")
    form = await request.form(max_files=1, max_fields=10)
    part = form.get("file")
    if not isinstance(part, UploadFile):
        raise _bad_body("Field 'file' is required and must be a file.")
    try:
        row = await files.upload(current.id, filename=part.filename, content=part)
    finally:
        await form.close()
    return FileOut.from_model(row)


@router.get("", response_model=FilePage, summary="List uploaded files, newest first")
async def list_files(
    current: CurrentUserDep,
    factory: FactoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> FilePage:
    try:
        decoded = repo.decode_cursor(cursor) if cursor else None
    except repo.InvalidCursorError:
        raise ApiError(400, "BAD_REQUEST", "Invalid cursor.") from None
    async with factory() as session:
        items, next_cursor = await repo.list_page(session, current.id, limit=limit, cursor=decoded)
    return FilePage(items=[FileOut.from_model(f) for f in items], next_cursor=next_cursor)


@router.get("/{file_id}", response_model=FileOut, summary="Get file metadata and status")
async def get_file(file_id: str, current: CurrentUserDep, factory: FactoryDep) -> FileOut:
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise _not_found() from None
    async with factory() as session:
        row = await repo.get(session, current.id, fid)
    if row is None:
        raise _not_found()
    return FileOut.from_model(row)
