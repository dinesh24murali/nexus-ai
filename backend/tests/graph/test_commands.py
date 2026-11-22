"""The slash-command parser (doc 08 section 2.6): every command, letter case, position and shape."""

import pytest
from app.graph.commands import (
    MAX_MEMORY_CHARS,
    MAX_TOPIC_CHARS,
    ParsedCommand,
    StaticReply,
    help_text,
    parse_command,
    unavailable_reply,
)

ALL = frozenset({"general_chat", "manage_memory", "generate_document", "deep_research"})


def parsed(text: str) -> ParsedCommand:
    result = parse_command(text)
    assert isinstance(result, ParsedCommand), result
    return result


@pytest.mark.parametrize(
    ("text", "intent", "slots"),
    [
        ("/pdf", "generate_document", {"source": "thread"}),
        ("/pdf last", "generate_document", {"source": "last_answer"}),
        ("/brief rust 2024", "generate_document", {"source": "topic", "topic": "rust 2024"}),
        (
            "/research how do vector databases scale?",
            "deep_research",
            {"topic": "how do vector databases scale?", "output": "chat"},
        ),
        ("/memory", "manage_memory", {"memory_action": "list"}),
        (
            "/memory remember I prefer Go",
            "manage_memory",
            {"memory_action": "save", "memory_content": "I prefer Go"},
        ),
        (
            "/memory forget my timezone",
            "manage_memory",
            {"memory_action": "forget", "memory_content": "my timezone"},
        ),
    ],
)
def test_every_command_selects_its_intent_and_slots(
    text: str, intent: str, slots: dict[str, str]
) -> None:
    command = parsed(text)
    assert (command.intent, command.slots) == (intent, slots)


@pytest.mark.parametrize(
    "text", ["/PDF", "/Pdf Last", "/MEMORY", "/Memory REMEMBER likes tea", "/Brief topic"]
)
def test_commands_are_case_insensitive(text: str) -> None:
    assert parse_command(text) is not None


def test_the_content_keeps_its_own_letter_case() -> None:
    assert parsed("/MEMORY Remember I Like TeA").slots["memory_content"] == "I Like TeA"


@pytest.mark.parametrize(
    "text",
    [
        "please run /pdf",
        " /pdf",
        "\n/pdf",
        "hello\n/memory",
        "/pdfx",
        "/memoryforget x",
        "/etc/hosts is a file",
        "/unknown",
        "/",
        "pdf",
    ],
)
def test_only_a_known_command_word_at_the_very_start_counts(text: str) -> None:
    assert parse_command(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "/pdf please",
        "/pdf last one",
        "/brief",
        "/brief   ",
        "/research",
        "/memory remember",
        "/memory forget",
        "/memory remember   ",
        "/memory list",
        "/memory clear everything",
        f"/brief {'x' * (MAX_TOPIC_CHARS + 1)}",
        f"/research {'x' * (MAX_TOPIC_CHARS + 1)}",
        f"/memory remember {'x' * (MAX_MEMORY_CHARS + 1)}",
    ],
)
def test_a_malformed_command_gets_a_usage_reply(text: str) -> None:
    reply = parse_command(text)
    assert isinstance(reply, StaticReply) and reply.kind == "usage"
    assert reply.text.startswith("Usage:")


def test_limits_are_inclusive() -> None:
    assert parsed(f"/brief {'x' * MAX_TOPIC_CHARS}")
    assert parsed(f"/memory remember {'y' * MAX_MEMORY_CHARS}")


def test_whitespace_between_the_parts_may_be_any_kind() -> None:
    assert parsed("/memory\tremember\nI prefer Go").slots["memory_content"] == "I prefer Go"
    assert parsed("/pdf   last  ").slots == {"source": "last_answer"}


def test_help_is_a_static_reply_that_lists_only_the_commands_that_work() -> None:
    reply = parse_command("/HELP", available=frozenset({"general_chat", "manage_memory"}))
    assert isinstance(reply, StaticReply) and reply.kind == "help"
    assert "/memory forget" in reply.text
    assert "/pdf" not in reply.text and "/research" not in reply.text


def test_help_lists_everything_once_every_pipeline_exists() -> None:
    text = help_text(ALL)
    for command in ("/pdf", "/pdf last", "/brief", "/research", "/memory"):
        assert command in text


def test_help_without_commands_still_explains_itself() -> None:
    assert "/" not in help_text(frozenset({"general_chat"})).split("Settings")[0]


def test_unavailable_replies_say_why() -> None:
    command = parsed("/pdf")
    assert "not available yet" in unavailable_reply(command, disabled=False).text
    assert "switched off" in unavailable_reply(command, disabled=True).text
