"""Builds the model request in the cache-friendly order (docs/02_outputs/08 section 4):

    1. `nexus.system`            fixed text, no variables
    2. `<summary>` message       changes only when `summarize` runs
    3. history                   the checkpointed messages, unmodified
    4. late context message      date, display name, memories: built per call, never stored
    5. the newest user message

Everything per-turn lives in item 4, so items 1-3 form a prefix that is byte-identical from one
turn to the next. The summary and context messages are human messages, not system messages: they
carry user-derived text and must not gain system authority (`nexus.system` tells the model to treat
them as data).
"""

from collections.abc import Sequence
from html import escape

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.graph.state import RecentFile
from app.llm.prompts import load_prompt

NONE_TEXT = "(none)"


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else NONE_TEXT


def _memory_lines(memories: Sequence[str]) -> str:
    """One bullet per memory, as plain text. A memory is user data that came from a model's
    reading of earlier chats: collapse it to one line and escape `<`, `>` and `&`, so it cannot
    close `</user_memory>` or open a tag of its own."""
    lines = [" ".join(escape(m, quote=False).split()) for m in memories]
    return _bullets([line for line in lines if line])


def _files_block(files: Sequence[RecentFile]) -> str:
    """One line per file: `id | name | date`. Names are user-supplied: the block sits inside the
    `<context>` data wrapper, which `nexus.system` tells the model never to obey."""
    return (
        "\n".join(f"{f['id']} | {f['filename']} | {f['uploaded_at']}" for f in files) or NONE_TEXT
    )


def build_request(
    messages: Sequence[BaseMessage],
    *,
    summary: str | None,
    memories: Sequence[str],
    today: str,
    user_display_name: str,
    recent_files: Sequence[RecentFile] = (),
) -> list[BaseMessage]:
    request: list[BaseMessage] = [SystemMessage(load_prompt("nexus.system").render())]
    if summary:
        request.append(HumanMessage(f"<summary>\n{summary}\n</summary>"))

    context = HumanMessage(
        load_prompt("nexus.context").render(
            today=today,
            user_display_name=user_display_name,
            memories_block=_memory_lines(memories),
            recent_files_block=_files_block(recent_files),
        )
    )
    newest_user = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=None
    )
    if newest_user is None:
        request.extend(messages)
        request.append(context)
    else:
        request.extend(messages[:newest_user])
        request.append(context)
        request.extend(messages[newest_user:])
    return request
