"""Parsing uploaded files (TASK-018). The malformed cases are the LLM-T12 corpus."""

from pathlib import Path

import pytest
from app.rag import formats
from app.rag.parsers import ParseError, parse
from docx import Document

from .builders import (
    docx_bytes,
    encrypted_pdf_bytes,
    pdf_bytes,
    write,
    zip_bomb_docx_bytes,
)

LONG = "The quarterly report shows revenue growth in every region this year. "


def test_pdf_pages_keep_their_numbers(tmp_path: Path) -> None:
    path = write(tmp_path / "f", pdf_bytes([LONG + "first", LONG + "second"]))

    doc = parse(path, formats.PDF)

    assert doc.page_count == 2
    assert [p.number for p in doc.pages] == [1, 2]
    assert "first" in doc.pages[0].text
    assert "second" in doc.pages[1].text


def test_pdf_without_text_is_refused_with_a_readable_reason(tmp_path: Path) -> None:
    path = write(tmp_path / "f", pdf_bytes(["", "", ""]))

    with pytest.raises(ParseError, match="scanned PDFs are not supported"):
        parse(path, formats.PDF)


def test_password_protected_pdf_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "f", encrypted_pdf_bytes([LONG]))

    with pytest.raises(ParseError, match="password-protected"):
        parse(path, formats.PDF)


def test_truncated_pdf_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "f", pdf_bytes([LONG])[:60])

    with pytest.raises(ParseError, match="could not be read"):
        parse(path, formats.PDF)


def test_pdf_over_the_page_limit_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "f", pdf_bytes([LONG] * 6))

    with pytest.raises(ParseError, match="more than 5 pages"):
        parse(path, formats.PDF, max_pages=5)


def test_text_over_the_character_limit_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "f", ("word " * 100).encode())

    with pytest.raises(ParseError, match="too much text"):
        parse(path, formats.TEXT, max_chars=200)


def test_docx_paragraphs_and_tables_are_read_in_document_order(tmp_path: Path) -> None:
    path = write(
        tmp_path / "f",
        docx_bytes(
            ["Intro paragraph", "Second paragraph"], table=[["Region", "Sales"], ["EU", "9"]]
        ),
    )

    doc = parse(path, formats.DOCX)

    assert len(doc.pages) == 1
    assert doc.pages[0].number is None
    assert doc.page_count is None
    text = doc.pages[0].text
    assert text.index("Intro paragraph") < text.index("Second paragraph") < text.index("Region")
    assert "EU" in text and "9" in text


def test_docx_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "f", b"PK\x03\x04 this is not really a zip")

    with pytest.raises(ParseError, match="could not be read"):
        parse(path, formats.DOCX)


def test_docx_zip_bomb_is_refused_before_it_is_expanded(tmp_path: Path) -> None:
    path = write(tmp_path / "f", zip_bomb_docx_bytes(300))
    assert path.stat().st_size < 2_000_000  # small on disk, huge once expanded

    with pytest.raises(ParseError, match="too large once unpacked"):
        parse(path, formats.DOCX)


def test_empty_docx_has_no_text(tmp_path: Path) -> None:
    path = write(tmp_path / "f", docx_bytes([]))

    with pytest.raises(ParseError, match="No extractable text"):
        parse(path, formats.DOCX)


def test_text_file_is_decoded_as_utf8(tmp_path: Path) -> None:
    path = write(tmp_path / "f", "Café au lait\n\nSecond paragraph".encode())

    doc = parse(path, formats.TEXT)

    assert "Café au lait" in doc.pages[0].text


def test_text_file_falls_back_to_latin1(tmp_path: Path) -> None:
    path = write(tmp_path / "f", "Café au lait".encode("latin-1"))

    assert "Café au lait" in parse(path, formats.TEXT).pages[0].text


def test_markdown_keeps_headings(tmp_path: Path) -> None:
    path = write(tmp_path / "f", b"# Title\n\nBody text here.\n\n## Section\n\nMore text.")

    text = parse(path, formats.MARKDOWN).pages[0].text

    assert "# Title" in text and "## Section" in text


def test_control_characters_are_stripped_and_whitespace_normalised(tmp_path: Path) -> None:
    path = write(tmp_path / "f", b"one\x00\x07 two   three\t\tfour\r\n\r\n\r\n\r\nfive")

    text = parse(path, formats.TEXT).pages[0].text

    assert "\x00" not in text and "\x07" not in text
    assert text == "one two three four\n\nfive"


def test_empty_text_file_has_no_text(tmp_path: Path) -> None:
    path = write(tmp_path / "f", b"  \n \n")

    with pytest.raises(ParseError, match="No extractable text"):
        parse(path, formats.TEXT)


def test_an_unknown_mime_type_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ParseError, match="not supported"):
        parse(write(tmp_path / "f", b"x"), "image/png")


def test_a_pdf_over_the_character_limit_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "f", pdf_bytes([LONG, LONG]))

    with pytest.raises(ParseError, match="too much text"):
        parse(path, formats.PDF, max_chars=100)


def test_a_docx_over_the_character_limit_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "f", docx_bytes([LONG, LONG]))

    with pytest.raises(ParseError, match="too much text"):
        parse(path, formats.DOCX, max_chars=100)


def test_docx_headings_become_markdown_headings_and_empty_paragraphs_vanish(
    tmp_path: Path,
) -> None:
    document = Document()
    document.add_heading("Annual report", 0)
    document.add_paragraph("")
    document.add_heading("Results", 2)
    document.add_paragraph("Revenue grew.")
    path = tmp_path / "f"
    document.save(str(path))

    text = parse(path, formats.DOCX).pages[0].text

    assert text == "# Annual report\n\n## Results\n\nRevenue grew."
