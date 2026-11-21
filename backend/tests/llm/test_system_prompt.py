"""What `nexus.system` must tell the model once file search exists (doc 08 sections 4 and 5)."""

import pytest
from app.llm.prompts import load_prompt

PROMPT = load_prompt("nexus.system")


def test_it_is_version_2_and_stays_free_of_per_turn_variables() -> None:
    assert PROMPT.version == 2
    assert PROMPT.variables == frozenset()  # fixed text, so the prefix cache keeps working


@pytest.mark.parametrize(
    "needle",
    [
        "search_my_files",  # it knows the tool
        "[1]",  # cites passages as [n]
        "not found in your files",  # and says so instead of guessing
        "<document",  # passages are named as data
    ],
)
def test_it_covers_file_search_and_citations(needle: str) -> None:
    assert needle in PROMPT.body


def test_it_no_longer_claims_it_cannot_open_files() -> None:
    assert "cannot browse the web, open files" not in PROMPT.body


def test_it_still_refuses_what_it_cannot_do() -> None:
    assert "browse the web" in PROMPT.body
    assert "run code" in PROMPT.body


def test_document_text_is_data_not_instructions() -> None:
    body = PROMPT.body
    assert "<document>" in body or "`<document>`" in body
    assert "instructions" in body
