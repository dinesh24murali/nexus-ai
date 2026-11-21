"""Turn an uploaded file into plain text, page by page (doc 08 section 6, step 1-2).

Runs in the worker only (threat LLM-T12): files are untrusted and parsers are the attack surface,
so every limit is checked before or while reading, never after. A `ParseError` carries a reason a
user can read; it is stored in `files.error` as is. Blocking and CPU-bound: call it from a thread.
"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.rag import formats

MAX_PAGES = 500
MAX_CHARS = 2_000_000
MIN_AVG_CHARS_PER_PAGE = 20  # below this a PDF is a scan or empty
MAX_UNPACKED_BYTES = 100 * 1024 * 1024  # DOCX is a zip: refuse bombs from the directory alone

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # everything except \t and \n
_SPACES = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_HEADING = re.compile(r"^Heading (\d)$")


class ParseError(Exception):
    """The file cannot be processed; `reason` is shown to the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Page:
    number: int | None  # 1-based for PDFs; None where the format has no pages
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    pages: list[Page]
    page_count: int | None


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def parse(
    path: Path, mime: str, *, max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS
) -> ParsedDocument:
    if mime == formats.PDF:
        return _parse_pdf(path, max_pages, max_chars)
    if mime == formats.DOCX:
        return _parse_docx(path, max_chars)
    if mime in (formats.TEXT, formats.MARKDOWN):
        return _parse_text(path, max_chars)
    raise ParseError("This file type is not supported.")


def _no_text() -> ParseError:
    return ParseError("No extractable text found in this file.")


def _too_much_text() -> ParseError:
    return ParseError("The file contains too much text (limit: 2 million characters).")


def _parse_pdf(path: Path, max_pages: int, max_chars: int) -> ParsedDocument:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ParseError("File is password-protected.")
        total = len(reader.pages)
        if total > max_pages:
            raise ParseError(f"The PDF has more than {max_pages} pages.")
        pages: list[Page] = []
        chars = 0
        for number, page in enumerate(reader.pages, start=1):
            text = normalize(page.extract_text() or "")
            chars += len(text)
            if chars > max_chars:
                raise _too_much_text()
            if text:
                pages.append(Page(number, text))
    except ParseError:
        raise
    except Exception:
        raise ParseError("The PDF could not be read; it may be damaged.") from None
    if total == 0 or chars / total < MIN_AVG_CHARS_PER_PAGE:
        raise ParseError("No extractable text — scanned PDFs are not supported yet.")
    return ParsedDocument(pages=pages, page_count=total)


def _parse_docx(path: Path, max_chars: int) -> ParsedDocument:
    try:
        with zipfile.ZipFile(path) as archive:
            unpacked = sum(info.file_size for info in archive.infolist())
        if unpacked > MAX_UNPACKED_BYTES:
            raise ParseError("The document is too large once unpacked.")
        document = Document(str(path))
        blocks: list[str] = []
        chars = 0
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                block = _paragraph_text(Paragraph(child, document))
            elif child.tag == qn("w:tbl"):
                block = _table_text(Table(child, document))
            else:
                continue
            if block:
                chars += len(block)
                if chars > max_chars:
                    raise _too_much_text()
                blocks.append(block)
    except ParseError:
        raise
    except Exception:
        raise ParseError("The document could not be read; it may be damaged.") from None
    text = normalize("\n\n".join(blocks))
    if not text:
        raise _no_text()
    return ParsedDocument(pages=[Page(None, text)], page_count=None)


def _paragraph_text(paragraph: Paragraph) -> str:
    text = paragraph.text.strip()
    if not text:
        return ""
    style = paragraph.style.name if paragraph.style is not None and paragraph.style.name else ""
    if style == "Title":
        return f"# {text}"
    if match := _HEADING.match(style):
        return f"{'#' * int(match.group(1))} {text}"
    return text


def _table_text(table: Table) -> str:
    rows = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _parse_text(path: Path, max_chars: int) -> ParsedDocument:
    raw = path.read_bytes()
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw.decode("latin-1")
    if len(decoded) > max_chars:
        raise _too_much_text()
    text = normalize(decoded)
    if not text:
        raise _no_text()
    return ParsedDocument(pages=[Page(None, text)], page_count=None)
