"""Redaction for traces, logs and eval datasets (AGENTS.md 3.8, ADR-0004).

`mask` is handed to the Langfuse client, which calls it on every input, output and metadata value
before export. It must be cheap, never raise and err on the side of over-redaction. It is a
safety net on top of not sending sensitive fields in the first place (user email is never sent).
"""

import hashlib
import hmac
import re
from typing import Any

_FAILED = "[REDACTION_FAILED]"

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_API_KEY = re.compile(
    r"\b(?:"
    r"sk-(?:proj-|ant-|svcacct-)?[A-Za-z0-9_-]{20,}"  # OpenAI / Anthropic
    r"|[ps]k-lf-[A-Za-z0-9-]{16,}"  # Langfuse
    r"|tvly-[A-Za-z0-9_-]{16,}"  # Tavily
    r"|gh[pousr]_[A-Za-z0-9]{30,}"  # GitHub
    r"|AKIA[0-9A-Z]{16}"  # AWS access key id
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack
    r")"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_CARD_CANDIDATE = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
_PHONE_CANDIDATE = re.compile(r"(?<![\w.])\+?\(?\d[\d ()./-]{7,}\d(?!\w)")


def _luhn_valid(digits: str) -> bool:
    """Luhn checksum: separates card numbers from other long digit strings (ids, timestamps)."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return "[CARD]" if 13 <= len(digits) <= 19 and _luhn_valid(digits) else match.group()


def _phone(match: re.Match[str]) -> str:
    text = match.group()
    digits = re.sub(r"\D", "", text)
    has_structure = text.startswith(("+", "(")) or re.search(r"[ ./-]", text)
    return "[PHONE]" if 9 <= len(digits) <= 15 and has_structure else text


def _mask_text(text: str) -> str:
    text = _JWT.sub("[JWT]", text)
    text = _API_KEY.sub("[API_KEY]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _IBAN.sub("[IBAN]", text)
    text = _CARD_CANDIDATE.sub(_card, text)
    return _PHONE_CANDIDATE.sub(_phone, text)


def _walk(value: Any, memo: dict[int, Any]) -> Any:
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, dict):
        if id(value) in memo:
            return memo[id(value)]
        out_dict: dict[Any, Any] = {}
        memo[id(value)] = out_dict
        for key, item in value.items():
            out_dict[key] = _walk(item, memo)
        return out_dict
    if isinstance(value, list):
        if id(value) in memo:
            return memo[id(value)]
        out_list: list[Any] = []
        memo[id(value)] = out_list
        out_list.extend(_walk(item, memo) for item in value)
        return out_list
    if isinstance(value, tuple):
        return tuple(_walk(item, memo) for item in value)
    return value


def mask(data: Any, **_: Any) -> Any:
    """Return `data` with sensitive substrings replaced; containers are copied, never mutated.

    Accepts (and ignores) extra keyword arguments because the Langfuse SDK calls `mask(data=...)`.
    """
    try:
        return _walk(data, {})
    except Exception:
        return _FAILED


def hash_user_id(user_id: str, *, secret: str) -> str:
    """Stable pseudonym for traces: HMAC-SHA256 keyed with APP_SECRET, truncated to 32 hex chars."""
    digest = hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()
    return digest[:32]
