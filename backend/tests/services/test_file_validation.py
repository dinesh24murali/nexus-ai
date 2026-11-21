"""File name cleaning and content sniffing (TASK-017); no database needed."""

from pathlib import Path

import pytest
from app.rag import formats
from app.services.files import MAX_FILENAME_CHARS, clean_filename, detect, extension_of

from tests.rag.builders import docx_bytes, pdf_bytes, write, zip_bomb_docx_bytes


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\me\\notes.txt", "notes.txt"),
        ("pass\x00wd\r\n.txt", "passwd.txt"),
        ("  spaced name.md  ", "spaced name.md"),
        ("dir/", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_filename(raw: str | None, clean: str) -> None:
    assert clean_filename(raw) == clean


def test_a_long_name_is_cut_but_keeps_its_extension() -> None:
    name = clean_filename("a" * 400 + ".pdf")

    assert len(name) == MAX_FILENAME_CHARS
    assert name.endswith(".pdf")


@pytest.mark.parametrize(
    ("name", "ext"),
    [("a.pdf", "pdf"), ("A.DOCX", "docx"), ("a.b.md", "md"), ("a.exe", None), (".txt", None),
     ("txt", None), ("a.", None)],
)  # fmt: skip
def test_extension_of(name: str, ext: str | None) -> None:
    assert extension_of(name) == ext


def test_detect_recognises_each_family(tmp_path: Path) -> None:
    assert detect(write(tmp_path / "a", pdf_bytes(["hello world"]))) == formats.PDF
    assert detect(write(tmp_path / "b", docx_bytes(["hello"]))) == formats.DOCX
    assert detect(write(tmp_path / "c", b"just some text\nwith lines\n")) == formats.TEXT
    assert detect(write(tmp_path / "d", "Café".encode("latin-1"))) == formats.TEXT


@pytest.mark.parametrize(
    "data",
    [b"", b"\x00\x01\x02\x03" * 100, b"\x7fELF\x02\x01\x01", b"PK\x03\x04junk", b"GIF89a\x00\x00"],
)
def test_detect_refuses_everything_else(tmp_path: Path, data: bytes) -> None:
    assert detect(write(tmp_path / "x", data)) is None


def test_detect_does_not_unpack_a_zip_bomb(tmp_path: Path) -> None:
    # It looks like a DOCX from the directory alone; the parser (not the sniffer) refuses its size.
    assert detect(write(tmp_path / "bomb", zip_bomb_docx_bytes(300))) == formats.DOCX
