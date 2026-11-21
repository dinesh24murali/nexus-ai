"""Accepting an upload (TASK-017, FS-004): validate, store on the uploads volume, record, enqueue.

Order matters. Cheap checks on the name come first, then the body is streamed to a `.part` file
while its size and SHA-256 are counted (so an oversize body stops at the limit, never in memory),
then the content is sniffed (the extension alone is not trusted), and only then does one
transaction check duplicates and the per-user limit, insert the row and enqueue `ingest_file`. The
user's file name is data: it is cleaned and stored in the row, never used as a path on disk.
"""

import asyncio
import hashlib
import os
import re
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import File
from app.db.repositories import files as repo
from app.graph.nodes.load_context import RecentFiles
from app.graph.state import RecentFile
from app.jobs import queue
from app.rag import formats
from app.rag.index import QdrantIndex, lazy_index

_log = structlog.get_logger("nexus.files")

CHUNK_BYTES = 1024 * 1024
MAX_FILENAME_CHARS = 255
INGEST_JOB = "ingest_file"
INGEST_ATTEMPTS = 3  # doc 08 section 9

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SNIFF_BYTES = 8192
_MAX_CONTROL_RATIO = 0.02  # a text file has almost no control characters
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"


class Readable(Protocol):
    async def read(self, size: int = -1, /) -> bytes: ...


def _unsupported() -> ApiError:
    return ApiError(415, "FILE_TYPE_UNSUPPORTED", "Only PDF, DOCX, TXT and MD files are supported.")


def clean_filename(raw: str | None) -> str:
    """The base name only (either path separator), without control characters, at most 255 chars
    (the extension is kept). Empty when nothing usable is left."""
    name = _CONTROL.sub("", raw or "")
    name = re.split(r"[\\/]", name)[-1].strip()
    if len(name) > MAX_FILENAME_CHARS:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) < 16:
            name = stem[: MAX_FILENAME_CHARS - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_FILENAME_CHARS]
    return name


def extension_of(name: str) -> str | None:
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or ext.lower() not in formats.EXTENSIONS:
        return None
    return ext.lower()


def detect(path: Path) -> str | None:
    """What the content is, judged by magic bytes: a MIME from `formats`, or None. Text files have
    no magic, so TEXT means "looks like text" and the extension picks plain text or markdown."""
    with path.open("rb") as handle:
        head = handle.read(_SNIFF_BYTES)
    if _PDF_MAGIC in head[:1024]:
        return formats.PDF
    if head.startswith(_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())  # the directory only: nothing is decompressed
        except zipfile.BadZipFile:
            return None
        if {"[Content_Types].xml", "word/document.xml"} <= names:
            return formats.DOCX
        return None
    if not head or b"\x00" in head:
        return None
    control = sum(1 for b in head if b < 32 and b not in (9, 10, 12, 13))
    return formats.TEXT if control / len(head) <= _MAX_CONTROL_RATIO else None


class FileService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        index: Callable[[], QdrantIndex] | None = None,
    ) -> None:
        self._factory = factory
        self._index = index or lazy_index(settings)
        self._root = settings.uploads_dir
        self._max_bytes = settings.max_upload_bytes
        self._max_files = settings.max_files_per_user

    def too_large(self) -> ApiError:
        limit_mb = self._max_bytes / (1024 * 1024)
        return ApiError(413, "FILE_TOO_LARGE", f"File is larger than {limit_mb:g} MB.")

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def upload(self, user_id: uuid.UUID, *, filename: str | None, content: Readable) -> File:
        name = clean_filename(filename)
        ext = extension_of(name)
        if ext is None:
            raise _unsupported()

        file_id = uuid.uuid4()
        relative = f"{user_id}/{file_id}"
        final = self._root / relative
        part = final.with_name(f"{file_id}.part")
        await asyncio.to_thread(part.parent.mkdir, parents=True, exist_ok=True)
        try:
            size, sha256 = await self._write(content, part)
            if size == 0:
                raise ApiError(422, "VALIDATION_FAILED", "The file is empty.")
            kind = await asyncio.to_thread(detect, part)
            expected = formats.EXTENSIONS[ext]
            if kind is None or (kind != expected and not _is_text_pair(kind, expected)):
                raise _unsupported()
            await asyncio.to_thread(os.replace, part, final)
            try:
                return await self._record(
                    user_id,
                    file_id=file_id,
                    filename=name,
                    mime_type=expected,
                    size_bytes=size,
                    sha256=sha256,
                    storage_path=relative,
                )
            except BaseException:
                await asyncio.to_thread(final.unlink, missing_ok=True)
                raise
        finally:
            await asyncio.to_thread(part.unlink, missing_ok=True)

    async def delete(self, user_id: uuid.UUID, file_id: uuid.UUID) -> None:
        """Remove a file everywhere (TASK-020): search index, queued ingestion, row, disk.

        The row is locked first, so a worker that is ingesting the file right now cannot mark it
        ready behind our back: its status write waits, then finds no row, and it removes whatever
        it indexed (see `ingest_file`). The vectors go before the row, inside the same
        transaction: if the vector store fails everything rolls back and the user can retry; the
        file is never left "deleted" but still searchable. The disk file goes after the commit
        (removing it first could leave a row pointing at nothing); if that fails the file is an
        orphan on disk that no query can reach, so it is logged, not raised.
        """
        index = self._index()
        async with self._factory() as session, session.begin():
            row = await repo.get_for_update(session, user_id, file_id)
            if row is None:
                raise ApiError(404, "NOT_FOUND", "File not found.")
            try:
                await index.ensure_collection()
                await index.delete_file(user_id, file_id)
            except Exception:
                _log.error("file_delete_index_failed", file_id=str(file_id), exc_info=True)
                raise
            await queue.cancel_queued(session, INGEST_JOB, user_id, "file_id", str(file_id))
            await repo.delete(session, user_id, file_id)
            storage_path = row.storage_path
        try:
            await asyncio.to_thread((self._root / storage_path).unlink, missing_ok=True)
        except OSError:
            _log.warning("file_delete_disk_failed", file_id=str(file_id), exc_info=True)
        _log.info("file_deleted", file_id=str(file_id))

    async def _write(self, content: Readable, part: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with part.open("wb") as out:
            while chunk := await content.read(CHUNK_BYTES):
                size += len(chunk)
                if size > self._max_bytes:
                    raise self.too_large()
                digest.update(chunk)
                await asyncio.to_thread(out.write, chunk)
        return size, digest.hexdigest()

    async def _record(
        self,
        user_id: uuid.UUID,
        *,
        file_id: uuid.UUID,
        filename: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        storage_path: str,
    ) -> File:
        try:
            async with self._factory() as session, session.begin():
                await self._check_room(session, user_id, sha256)
                row = await repo.create(
                    session,
                    user_id,
                    file_id=file_id,
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    storage_path=storage_path,
                )
                await queue.enqueue(
                    session,
                    INGEST_JOB,
                    {"file_id": str(file_id)},
                    f"ingest:{file_id}:{sha256}",
                    user_id=user_id,
                    max_attempts=INGEST_ATTEMPTS,
                )
        except IntegrityError:
            # Only the (user, sha256) constraint can fire here, and the user lock makes it a
            # same-user race that the check above normally catches first.
            async with self._factory() as session:
                existing = await repo.get_by_sha(session, user_id, sha256)
            raise _duplicate(existing.id if existing else None) from None
        _log.info("file_uploaded", file_id=str(file_id), size_bytes=size_bytes, mime=mime_type)
        return row

    async def _check_room(self, session: AsyncSession, user_id: uuid.UUID, sha256: str) -> None:
        await repo.lock_user(session, user_id)
        existing = await repo.get_by_sha(session, user_id, sha256)
        if existing is not None:
            raise _duplicate(existing.id)
        if await repo.count(session, user_id) >= self._max_files:
            raise ApiError(
                409,
                "FILE_LIMIT_REACHED",
                f"You can keep up to {self._max_files} files. Delete one to upload another.",
            )


def _is_text_pair(kind: str, expected: str) -> bool:
    return kind == formats.TEXT and expected in (formats.TEXT, formats.MARKDOWN)


def _duplicate(existing_id: uuid.UUID | None) -> ApiError:
    return ApiError(
        409,
        "FILE_DUPLICATE",
        "You have already uploaded this file.",
        details={"file_id": str(existing_id)} if existing_id else None,
    )


RECENT_FILES = 5  # how many files the late context message lists (doc 08 section 6)


def recent_files_for(factory: async_sessionmaker[AsyncSession]) -> RecentFiles:
    """The lookup `load_context` uses: the caller's newest ready files as (id, name, date)."""

    async def recent(user_id: uuid.UUID) -> list[RecentFile]:
        async with factory() as session:
            rows = await repo.recent_ready(session, user_id, limit=RECENT_FILES)
        return [
            {
                "id": str(r.id),
                "filename": r.filename,
                "uploaded_at": r.created_at.date().isoformat(),
            }
            for r in rows
        ]

    return recent
