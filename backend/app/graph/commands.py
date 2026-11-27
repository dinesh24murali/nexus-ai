"""Slash commands (docs/02_outputs/08 section 2.6): parsed by code, never by a model.

A command counts only at the very start of the message, in any letter case, and only as a whole
word: `/pdfx` and `/etc/hosts` are ordinary text. A known command with the wrong shape gets a short
usage reply. The parser sets the intent and the slots; whether the pipeline exists or is switched
off is the caller's business (`unavailable_reply`).

Pure: no I/O, so it is tested without a graph.
"""

from dataclasses import dataclass
from typing import Any, Literal

from app.graph.intents import Intent

MAX_TOPIC_CHARS = 200  # the `topic` slot (doc 08 section 2.1)
MAX_MEMORY_CHARS = 500  # the `memory_content` slot, and the API's memory limit

Kind = Literal["help", "usage", "unavailable"]


@dataclass(frozen=True)
class ParsedCommand:
    """A command that selects a pipeline."""

    name: str
    intent: Intent
    slots: dict[str, Any]


@dataclass(frozen=True)
class StaticReply:
    """An answer that needs no pipeline and no model: the help text or a usage message."""

    kind: Kind
    text: str


_USAGE: dict[str, str] = {
    "pdf": (
        "Usage: `/pdf` turns this conversation into a PDF, "
        "`/pdf last` turns my last answer into one."
    ),
    "brief": (
        "Usage: `/brief <topic>` writes a short PDF brief about a topic (up to 200 characters)."
    ),
    "research": (
        "Usage: `/research <question>` researches a question on the web (up to 200 characters)."
    ),
    "memory": (
        "Usage: `/memory` lists what I remember about you, `/memory remember <text>` saves "
        "something (up to 500 characters) and `/memory forget <text>` removes a memory after "
        "you confirm."
    ),
}

_ENTRY: dict[str, str] = {
    "general_chat": "",
    "manage_memory": (
        "- `/memory`: list what I remember about you\n"
        "- `/memory remember <text>`: save something\n"
        "- `/memory forget <text>`: remove a memory (I ask you first)\n"
    ),
    "generate_document": (
        "- `/pdf`: turn this conversation into a PDF\n"
        "- `/pdf last`: turn my last answer into a PDF\n"
        "- `/brief <topic>`: write a short PDF brief about a topic\n"
    ),
    "deep_research": "- `/research <question>`: research a question on the web\n",
}


def help_text(available: frozenset[str]) -> str:
    """What `/help` answers: the capabilities of this build and the commands that work in it."""
    commands = "".join(
        _ENTRY[intent]
        for intent in ("manage_memory", "generate_document", "deep_research")
        if intent in available
    )
    parts = [
        "I'm Nexus. I can chat, explain, write and summarise, search the files you upload, "
        "search the web when you approve it, and do exact arithmetic.",
    ]
    if commands:
        parts.append("Commands (they work at the start of a message):\n\n" + commands.rstrip())
    parts.append("Manage what I remember about you in Settings → Memory.")
    return "\n\n".join(parts)


def _split(text: str) -> tuple[str, str] | None:
    """(command word in lower case, the rest) when `text` starts with `/word`, else None."""
    if not text.startswith("/"):
        return None
    end = 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[1:end].lower(), text[end:].strip()


def _usage(name: str) -> StaticReply:
    return StaticReply("usage", _USAGE[name])


def parse_command(
    text: str, *, available: frozenset[str] = frozenset()
) -> ParsedCommand | StaticReply | None:
    """The command at the start of `text`, a usage reply, or None when it is not a command.

    `available` only feeds the `/help` text."""
    split = _split(text)
    if split is None:
        return None
    word, rest = split
    match word:
        case "help":
            return StaticReply("help", help_text(available))
        case "pdf":
            if not rest:
                return ParsedCommand("pdf", "generate_document", {"source": "thread"})
            if rest.lower() == "last":
                return ParsedCommand("pdf last", "generate_document", {"source": "last_answer"})
            return _usage("pdf")
        case "brief":
            if not rest or len(rest) > MAX_TOPIC_CHARS:
                return _usage("brief")
            return ParsedCommand("brief", "generate_document", {"source": "topic", "topic": rest})
        case "research":
            if not rest or len(rest) > MAX_TOPIC_CHARS:
                return _usage("research")
            return ParsedCommand("research", "deep_research", {"topic": rest, "output": "chat"})
        case "memory":
            return _memory(rest)
    return None


def _memory(rest: str) -> ParsedCommand | StaticReply:
    if not rest:
        return ParsedCommand("memory", "manage_memory", {"memory_action": "list"})
    parts = rest.split(None, 1)  # any whitespace, so a tab or a line break works as a space
    verb = parts[0]
    action = {"remember": "save", "forget": "forget"}.get(verb.lower())
    content = parts[1].strip() if len(parts) > 1 else ""
    if action is None or not content or len(content) > MAX_MEMORY_CHARS:
        return _usage("memory")
    return ParsedCommand(
        f"memory {verb.lower()}",
        "manage_memory",
        {"memory_action": action, "memory_content": content},
    )


def unavailable_reply(command: ParsedCommand, *, disabled: bool) -> StaticReply:
    """The explanation for a command whose pipeline is switched off or not built yet."""
    if disabled:
        text = (
            f"`/{command.name}` is switched off right now. "
            "You can still ask me in a normal message."
        )
    else:
        text = f"`/{command.name}` is not available yet."
    return StaticReply("unavailable", text)
