"""Token counting for context budgets (summarisation trigger, history window).

Uses the `o200k_base` encoding. tiktoken downloads its encoding file on first use; if that fails
(offline CI, locked-down container) counting falls back to a four-characters-per-token estimate and
logs one warning, instead of failing chat. The Docker image bakes the file in (backend/Dockerfile).
"""

import json
from collections.abc import Sequence
from functools import lru_cache

import structlog
import tiktoken
from langchain_core.messages import AIMessage, BaseMessage

_log = structlog.get_logger("nexus.llm.tokens")

MESSAGE_OVERHEAD = 4  # role + separators per message, as in OpenAI's own counting guidance


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding | None:
    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception:
        _log.warning("tiktoken_encoding_unavailable_using_estimate", exc_info=True)
        return None


def encoding_available() -> bool:
    return _encoding() is not None


def count_text(text: str) -> int:
    if not text:
        return 0
    encoding = _encoding()
    if encoding is None:
        return max(1, len(text) // 4)
    return len(encoding.encode(text, disallowed_special=()))


def count_message(message: BaseMessage) -> int:
    total = MESSAGE_OVERHEAD + count_text(message.text)
    if isinstance(message, AIMessage):
        for call in message.tool_calls:
            total += count_text(call["name"]) + count_text(json.dumps(call["args"], sort_keys=True))
    return total


def count_messages(messages: Sequence[BaseMessage]) -> int:
    return sum(count_message(m) for m in messages)
