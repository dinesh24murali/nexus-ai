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


def remove(uploads_dir: Path, relative: str | None) -> bool:
    """Delete one PDF (blocking: call it from a thread in async code). True if one was removed."""
    if not relative:
        return False
    root = uploads_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        return False
    with contextlib.suppress(OSError):
        target.unlink()
        return True
    return False
