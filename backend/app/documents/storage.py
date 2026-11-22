"""Where the PDFs live: `<UPLOADS_DIR>/<user id>/documents/<document id>.pdf`.

Under the user's own directory on purpose: account deletion removes that whole directory
(`purge_user`), so a PDF can never outlive its owner. The database stores the path relative to
`UPLOADS_DIR`; `remove` refuses a path that would leave it.
"""

import contextlib
import uuid
from pathlib import Path


def relative_path(user_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"{user_id}/documents/{document_id}.pdf"


def resolve(uploads_dir: Path, relative: str | None) -> Path | None:
    """The absolute path of a stored PDF, or None when `relative` is empty or would leave
    `UPLOADS_DIR` (the one rule that `remove` and the download share). Blocking: it touches the
    file system, so call it from a thread in async code."""
    if not relative:
        return None
    root = uploads_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        return None
    return target


def remove(uploads_dir: Path, relative: str | None) -> bool:
    """Delete one PDF (blocking: call it from a thread in async code). True if one was removed."""
    target = resolve(uploads_dir, relative)
    if target is None:
        return False
    with contextlib.suppress(OSError):
        target.unlink()
        return True
    return False
