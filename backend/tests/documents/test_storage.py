"""Where a stored PDF may be read from or removed (TASK-042 serves what TASK-041 wrote)."""

import uuid
from pathlib import Path

import pytest
from app.documents import storage


def test_the_relative_path_lives_under_the_users_own_directory() -> None:
    user, doc = uuid.uuid4(), uuid.uuid4()
    assert storage.relative_path(user, doc) == f"{user}/documents/{doc}.pdf"


def test_resolve_finds_a_file_under_the_uploads_directory(tmp_path: Path) -> None:
    assert (
        storage.resolve(tmp_path, "u/documents/d.pdf") == (tmp_path / "u/documents/d.pdf").resolve()
    )


@pytest.mark.parametrize(
    "relative",
    [None, "", ".", "../outside.pdf", "u/../../outside.pdf", "/etc/passwd", "u/documents/../../.."],
)
def test_resolve_refuses_anything_that_leaves_the_directory(
    tmp_path: Path, relative: str | None
) -> None:
    (tmp_path / "uploads").mkdir()
    assert storage.resolve(tmp_path / "uploads", relative) is None


def test_a_symlink_pointing_outside_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"x")
    root = tmp_path / "uploads"
    (root / "u").mkdir(parents=True)
    (root / "u" / "link.pdf").symlink_to(outside)

    assert storage.resolve(root, "u/link.pdf") is None
    assert storage.remove(root, "u/link.pdf") is False
    assert outside.exists()


def test_remove_deletes_one_file_and_says_so(tmp_path: Path) -> None:
    target = tmp_path / "u" / "d.pdf"
    target.parent.mkdir()
    target.write_bytes(b"x")
    assert storage.remove(tmp_path, "u/d.pdf") is True
    assert not target.exists()
    assert storage.remove(tmp_path, "u/d.pdf") is False
