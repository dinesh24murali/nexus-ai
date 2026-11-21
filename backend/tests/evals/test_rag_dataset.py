"""`rag.golden` and its fixture corpus: the references must be true, checked against the real
parser and chunker (no network, no model)."""

import json
from collections import Counter
from pathlib import Path

import pytest
from evals.corpus import chunks_of, load_manifest, mime_of, write_file
from evals.datasets import DATASETS_DIR, DatasetError, RagCase, load_dataset

NAME = "rag.golden"


def cases() -> list[RagCase]:
    return load_dataset(NAME, RagCase).cases


def test_the_dataset_has_forty_cases_of_four_kinds_and_a_ten_case_smoke_subset() -> None:
    data = cases()
    assert len(data) == 40
    assert Counter(c.kind for c in data) == {
        "fact": 23,
        "multi": 6,
        "whole_doc": 4,
        "unanswerable": 7,
    }
    assert len(load_dataset(NAME, RagCase, smoke_only=True).cases) == 10


def test_every_whole_document_case_is_in_the_smoke_subset() -> None:
    """They answer the open design question (per-file synopsis), so a PR run must include them."""
    smoke = {c.id for c in load_dataset(NAME, RagCase, smoke_only=True).cases}
    assert {c.id for c in cases() if c.kind == "whole_doc"} <= smoke


def test_answerable_cases_point_at_chunks_and_unanswerable_ones_at_none() -> None:
    for case in cases():
        if case.kind == "unanswerable":
            assert not case.answerable and case.reference_chunks == [] and case.evidence == []
        else:
            assert case.answerable and case.reference_chunks and case.evidence, case.id


def test_every_referenced_file_is_in_the_manifest() -> None:
    names = {e.name for e in load_manifest()}
    for case in cases():
        for ref in case.reference_chunks:
            assert ref.file in names, f"{case.id}: {ref.file}"


def test_every_evidence_phrase_is_really_in_the_referenced_file_and_page(
    tmp_path: Path,
) -> None:
    """Parses and chunks the built fixture files exactly as ingestion does."""
    text_of: dict[tuple[str, int | None], str] = {}
    for entry in load_manifest():
        path = write_file(entry, tmp_path)
        for chunk in chunks_of(path, mime_of(entry)):
            key = (entry.name, chunk.page)
            text_of[key] = text_of.get(key, "") + "\n" + chunk.text
    for case in (c for c in cases() if c.answerable):
        referenced = " ".join(
            text_of.get((ref.file, ref.page), "") for ref in case.reference_chunks
        )
        assert referenced.strip(), f"{case.id}: no chunk for {case.reference_chunks}"
        for phrase in case.evidence:
            assert phrase in referenced, f"{case.id}: {phrase!r} not on the referenced page"


def test_the_pdf_fixtures_have_the_pages_the_references_use(tmp_path: Path) -> None:
    pages = {}
    for entry in load_manifest():
        if entry.format == "pdf":
            pages[entry.name] = {
                c.page for c in chunks_of(write_file(entry, tmp_path), mime_of(entry))
            }
    assert pages == {"q3-business-review.pdf": {1, 2, 3}, "vendor-contract-summary.pdf": {1, 2}}


def test_the_newest_file_is_the_battery_brief_so_just_uploaded_means_it() -> None:
    assert load_manifest()[-1].name == "research-brief-battery-storage.md"


def test_ids_are_unique_and_a_bad_record_is_rejected(tmp_path: Path) -> None:
    good = json.loads((DATASETS_DIR / f"{NAME}.jsonl").read_text().splitlines()[0])
    (tmp_path / f"{NAME}.jsonl").write_text(json.dumps({**good, "kind": "essay"}) + "\n")
    with pytest.raises(DatasetError, match=r"rag\.golden\.jsonl:1"):
        load_dataset(NAME, RagCase, directory=tmp_path)
    (tmp_path / f"{NAME}.jsonl").write_text(json.dumps({**good, "answerable": False}) + "\n")
    with pytest.raises(DatasetError):  # a fact case cannot be unanswerable
        load_dataset(NAME, RagCase, directory=tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        {"reference_chunks": []},  # answerable but nothing to find
        {"kind": "unanswerable", "answerable": False},  # unanswerable but has references
    ],
)
def test_inconsistent_references_are_rejected(tmp_path: Path, change: dict[str, object]) -> None:
    good = json.loads((DATASETS_DIR / f"{NAME}.jsonl").read_text().splitlines()[0])
    (tmp_path / f"{NAME}.jsonl").write_text(json.dumps({**good, **change}) + "\n")
    with pytest.raises(DatasetError, match=r"rag\.golden\.jsonl:1"):
        load_dataset(NAME, RagCase, directory=tmp_path)
