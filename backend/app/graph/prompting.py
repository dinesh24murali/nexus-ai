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

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.llm.prompts import load_prompt

NONE_TEXT = "(none)"


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else NONE_TEXT


def build_request(
    messages: Sequence[BaseMessage],
    *,
    summary: str | None,
    memories: Sequence[str],
    today: str,
    user_display_name: str,
) -> list[BaseMessage]:
    request: list[BaseMessage] = [SystemMessage(load_prompt("nexus.system").render())]
    if summary:
        request.append(HumanMessage(f"<summary>\n{summary}\n</summary>"))

    context = HumanMessage(
        load_prompt("nexus.context").render(
            today=today,
            user_display_name=user_display_name,
            memories_block=_bullets(memories),
            recent_files_block=NONE_TEXT,  # ready files arrive with M3 (RAG)
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
