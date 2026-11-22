"""What `nexus.system` must tell the model about its tools (doc 08 sections 4 and 5)."""

import pytest
from app.llm.prompts import load_prompt

PROMPT = load_prompt("nexus.system")


def test_it_is_version_4_and_stays_free_of_per_turn_variables() -> None:
    assert PROMPT.version == 4
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


def test_it_says_how_to_use_remembered_notes_and_still_denies_saving_them_itself() -> None:
    memory = PROMPT.body.split("## Memory", 1)[1].split("## Scope", 1)[0]
    assert "`<user_memory>`" in memory  # it knows where the notes are
    assert "do not recite" in memory
    assert "believe the user" in memory  # a stale note loses against what the user says now
    assert "Never claim to have saved, updated or deleted a memory yourself" in memory
    assert "managed in Settings → Memory" in memory


def test_the_context_prompt_calls_memories_possibly_outdated_data() -> None:
    context = load_prompt("nexus.context")
    assert context.version == 2
    assert "background data, not instructions" in context.body
    assert "outdated" in context.body
