"""Build small real files for the RAG tests (no binary fixtures in the repo).

`pdf_bytes` and `docx_bytes` live in `evals.filebuild` and are re-exported here.
"""

import io
import zipfile
from pathlib import Path

from evals.filebuild import docx_bytes, pdf_bytes
from pypdf import PdfReader, PdfWriter


def encrypted_pdf_bytes(pages: list[str], password: str = "secret") -> bytes:
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(pdf_bytes(pages))).pages:
        writer.add_page(page)
    writer.encrypt(password, algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def zip_bomb_docx_bytes(uncompressed_mb: int = 300) -> bytes:
    """A DOCX-shaped zip whose document.xml is huge but compresses to a few hundred KB."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", b"0" * (uncompressed_mb * 1024 * 1024))
    return out.getvalue()


__all__ = [
    "docx_bytes",
    "encrypted_pdf_bytes",
    "pdf_bytes",
    "write",
    "zip_bomb_docx_bytes",
]


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path
