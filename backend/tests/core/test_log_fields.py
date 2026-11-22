"""Log lint (LLM-T10, backend/AGENTS.md "Logging"): a log call never carries what a user wrote or
what identifies them. App logs are metadata only (route, status, latency, ids, counts).

The check reads every `_log.<level>(...)` call in `app/` and refuses a field whose name says it
holds content. It cannot see what a differently named field holds, so it is a tripwire for the
usual mistakes, not a proof; the redaction tests and `test_logging.py` cover the rest.
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "bind"}
_LOGGER_NAMES = {"_log", "log", "logger", "_logger"}

# Field names that mean "text a user wrote", a credential, or a person.
_FORBIDDEN = {
    "content",
    "text",
    "message_text",
    "body",
    "prompt",
    "query",
    "answer",
    "memory",
    "memory_text",
    "filename",
    "file_name",
    "email",
    "password",
    "token",
    "secret",
    "api_key",
    "authorization",
    "cookie",
    "headers",
    "display_name",
    "invite_code",
}


def _calls_in(tree: ast.AST) -> list[tuple[int, str, list[str]]]:
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        target = node.func.value
        if (
            node.func.attr in _LEVELS
            and isinstance(target, ast.Name)
            and target.id in _LOGGER_NAMES
        ):
            found.append((node.lineno, node.func.attr, [k.arg for k in node.keywords if k.arg]))
    return found


def _log_calls() -> list[tuple[Path, int, str, list[str]]]:
    return [
        (path, line, level, keywords)
        for path in sorted(APP.rglob("*.py"))
        for line, level, keywords in _calls_in(ast.parse(path.read_text(encoding="utf-8")))
    ]


def test_the_scan_finds_the_log_calls_it_is_meant_to_check() -> None:
    calls = _log_calls()
    assert len(calls) > 20
    assert any(level == "exception" for _, _, level, _ in calls)


def test_no_log_call_has_a_field_that_names_user_content_or_a_credential() -> None:
    offenders = [
        f"{path.relative_to(APP.parent)}:{line} {level}({', '.join(sorted(bad))})"
        for path, line, level, keywords in _log_calls()
        if (bad := _FORBIDDEN.intersection(keywords))
    ]
    assert not offenders, "log fields that would carry content:\n" + "\n".join(offenders)


@pytest.mark.parametrize("name", sorted(_FORBIDDEN))
def test_the_scan_catches_a_forbidden_field_and_ignores_a_metadata_one(name: str) -> None:
    ((_, _, bad),) = _calls_in(ast.parse(f"_log.info('x', {name}=value)"))
    assert _FORBIDDEN.intersection(bad)
    ((_, _, ok),) = _calls_in(ast.parse("_log.warning('x', count=1, user_id=u, status=2)"))
    assert not _FORBIDDEN.intersection(ok)
