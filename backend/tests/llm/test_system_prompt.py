"""What `nexus.system` must tell the model about its tools (doc 08 sections 4 and 5)."""

import pytest
from app.llm.prompts import load_prompt

PROMPT = load_prompt("nexus.system")


def test_it_is_version_3_and_stays_free_of_per_turn_variables() -> None:
    assert PROMPT.version == 3
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


def test_it_no_longer_claims_it_cannot_open_files_or_browse_the_web() -> None:
    assert "cannot browse the web, open files" not in PROMPT.body
    assert "You cannot browse the web" not in PROMPT.body


def test_it_still_refuses_what_it_cannot_do() -> None:
    assert "run code" in PROMPT.body
    assert "do not have it" in PROMPT.body  # a tool that is not in the list does not exist


@pytest.mark.parametrize(
    "needle",
    [
        "`web_search`",
        "`calculator`",
        "greetings",  # no tool for small talk
        "general knowledge",
        "recent events",  # when the web is the right tool
        "approve",  # the user approves every search
        "declined",  # and a declined search is answered without it
        "did not search the web",
    ],
)
def test_it_has_the_tool_routing_rules(needle: str) -> None:
    assert needle in PROMPT.body


def test_web_results_are_data_not_instructions_and_are_cited_by_their_index() -> None:
    body = PROMPT.body
    assert "<web_result" in body
    assert "data" in body.split("<web_result", 1)[1]
    assert "Never cite a number that was not returned" in body


def test_document_text_is_data_not_instructions() -> None:
    body = PROMPT.body
    assert "<document>" in body or "`<document>`" in body
    assert "instructions" in body


def test_it_does_not_offer_other_modes() -> None:
    assert "Never offer to create a PDF" in PROMPT.body
