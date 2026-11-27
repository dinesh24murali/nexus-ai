"""The structured document and what is gathered for it (doc 08 sections 2.5 and 4).

`DocumentDraft` is what the `nexus.document` prompt returns (strict structured output, so no length
rules in the schema: `clean_draft` applies them afterwards, cutting instead of failing). A draft for
`last_answer` is built by `draft_from_answer` with no model call. `thread_material` and
`web_material` build the text a model composes from; it is data, never instructions, so it is
escaped and wrapped in tags.
"""

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from app.llm.tokens import count_text

MAX_TITLE_CHARS = 120
MAX_SECTIONS = 12
MAX_HEADING_CHARS = 120
MAX_BODY_CHARS = 6_000
MAX_SOURCES = 20
MAX_SOURCE_TITLE_CHARS = 200
MAX_URL_CHARS = 500
THREAD_MATERIAL_TOKENS = 16_000  # doc 08 section 2.5: the most that is sent to the model
DEFAULT_TITLE = "Nexus document"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class DraftError(Exception):
    """The draft has nothing usable in it."""


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    body_md: str


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    title: str
    url: str | None


class DocumentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    sections: list[Section]
    sources: list[Source]


def _line(text: str, limit: int) -> str:
    return " ".join(_CONTROL.sub(" ", text).split())[:limit].rstrip()


def _body(text: str) -> str:
    return _CONTROL.sub("", text).strip()[:MAX_BODY_CHARS].rstrip()


def _web_url(url: str | None) -> str | None:
    if not url or len(url) > MAX_URL_CHARS:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    ok = (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and not any(ch.isspace() for ch in url)
    )
    return url if ok else None


def clean_draft(draft: DocumentDraft, *, title_hint: str | None = None) -> DocumentDraft:
    """The draft within its limits. Raises `DraftError` when no section has any text."""
    sections = [
        Section(heading=_line(s.heading, MAX_HEADING_CHARS) or "Section", body_md=_body(s.body_md))
        for s in draft.sections
    ]
    sections = [s for s in sections if s.body_md][:MAX_SECTIONS]
    if not sections:
        raise DraftError("the draft has no text")
    seen: set[int] = set()
    sources: list[Source] = []
    for source in draft.sources:
        if source.index < 1 or source.index in seen:
            continue
        seen.add(source.index)
        title = _line(source.title, MAX_SOURCE_TITLE_CHARS)
        if title:
            sources.append(Source(index=source.index, title=title, url=_web_url(source.url)))
    title = _line(draft.title, MAX_TITLE_CHARS) or _line(title_hint or "", MAX_TITLE_CHARS)
    return DocumentDraft(
        title=title or DEFAULT_TITLE, sections=sections, sources=sources[:MAX_SOURCES]
    )


_HEADING = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def draft_from_answer(
    text: str, *, title_hint: str | None, citations: Sequence[Mapping[str, Any]] = ()
) -> DocumentDraft:
    """A previous answer as a document, with no model: a leading `# Title` is the title, every
    other `#`..`###` heading starts a section, text before the first heading is the "Answer"
    section, and the answer's own citations become the sources."""
    title: str | None = None
    sections: list[Section] = []
    heading: str | None = None
    lines: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body or heading is not None:
            sections.append(Section(heading=heading or "Answer", body_md=body))

    for line in text.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
        match = None if in_code else _HEADING.match(line)
        if match is None:
            lines.append(line)
            continue
        first = title is None and heading is None and not any(x.strip() for x in lines)
        if first and not sections and len(match.group(1)) == 1:
            title = match.group(2)
            lines.clear()
            continue
        flush()
        heading, lines = match.group(2), []
    flush()
    sources = [
        Source(
            index=int(c["index"]),
            title=str(c.get("title") or "Source")
            + (f" p.{c['page']}" if c.get("source_type") == "file" and c.get("page") else ""),
            url=str(c["url"]) if c.get("source_type") == "web" and c.get("url") else None,
        )
        for c in citations
        if isinstance(c.get("index"), int)
    ]
    return clean_draft(
        DocumentDraft(title=title or title_hint or "", sections=sections, sources=sources)
    )


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def thread_material(
    summary: str | None,
    messages: Sequence[tuple[str, str]],
    *,
    max_tokens: int = THREAD_MATERIAL_TOKENS,
) -> str:
    """The conversation as data for the model: the rolling summary, then the newest messages that
    fit in `max_tokens` (oldest first). `messages` are (role, text). Every text is escaped, so
    nothing in it can close the tag it sits in."""
    budget = max_tokens
    summary_part = ""
    if summary and summary.strip():
        summary_part = f"<summary>\n{_escape(summary.strip())}\n</summary>\n"
        budget -= count_text(summary_part)
    kept: list[str] = []
    for role, text in reversed(messages):
        who = "user" if role == "user" else "assistant"
        block = f'<message role="{who}">\n{_escape(text.strip())}\n</message>'
        cost = count_text(block)
        if cost > budget:
            break
        budget -= cost
        kept.append(block)
    return f"{summary_part}<conversation>\n" + "\n".join(reversed(kept)) + "\n</conversation>"
