"""Untrusted Markdown to sanitised HTML for the PDF (doc 10 LLM-T18). Pure: no weasyprint here.

The text comes from a conversation, web results or a model's draft, so it is treated as hostile:

1. raw HTML in the Markdown is escaped, never parsed (`html: False`);
2. the `image` rule is off, so `![x](http://...)` never becomes an `<img>` the renderer could fetch;
3. a link survives only with an `http(s)` address, and is followed by that address in full, so a
   reader of the PDF sees where it goes (a link label can say anything); every other link is
   reduced to its text;
4. what is left goes through an allowlist sanitiser (`nh3`), which is the last line of defence;
5. headings inside a section are pushed down two levels so they cannot outrank the section heading.

The renderer's fetcher (`fetcher.py`) is a separate layer: even if markup got through here, nothing
outside the bundled files could be loaded.
"""

import html
from datetime import date
from urllib.parse import urlsplit

import nh3
from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

from app.documents.bundled import STYLESHEET_URL
from app.documents.draft import DocumentDraft

ALLOWED_TAGS = frozenset(
    {
        "p", "br", "hr", "strong", "em", "code", "pre", "blockquote", "ul", "ol", "li", "a",
        "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td",
    }
)  # fmt: skip
ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_HEADING_LEVEL = 6
HEADING_SHIFT = 2  # `#` in a section becomes `h3`: the section heading is the `h2`


def _is_web_link(href: str | None) -> bool:
    if not href or any(ch.isspace() or ord(ch) < 32 for ch in href):
        return False
    try:
        parts = urlsplit(href)
    except ValueError:
        return False
    return parts.scheme in ALLOWED_SCHEMES and bool(parts.hostname)


def _rewrite(state: StateCore) -> None:
    """Shift headings down and reduce links: keep a web link and print its address after it."""
    for token in state.tokens:
        if token.type in ("heading_open", "heading_close"):
            level = min(int(token.tag[1]) + HEADING_SHIFT, MAX_HEADING_LEVEL)
            token.tag = f"h{level}"
        if token.type != "inline" or not token.children:
            continue
        rewritten: list[Token] = []
        open_hrefs: list[str | None] = []
        for child in token.children:
            if child.type == "link_open":
                href = child.attrGet("href")
                href = str(href) if href is not None else None
                open_hrefs.append(href if _is_web_link(href) else None)
                if open_hrefs[-1] is None:
                    continue  # not a web link: keep the label, drop the link
                child.attrs = {"href": str(href)}
                rewritten.append(child)
            elif child.type == "link_close":
                href = open_hrefs.pop() if open_hrefs else None
                if href is None:
                    continue
                rewritten.append(child)
                address = Token("text", "", 0)
                address.content = f" ({href})"
                rewritten.append(address)
            else:
                rewritten.append(child)
        token.children = rewritten


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": False})
    md.enable("table").disable("image")
    md.core.ruler.push("nexus_rewrite", _rewrite)
    return md


_MD = _markdown()


def markdown_to_html(text: str) -> str:
    """Sanitised HTML for one section body."""
    return nh3.clean(
        _MD.render(text),
        tags=set(ALLOWED_TAGS),
        attributes={"a": {"href"}},
        url_schemes=set(ALLOWED_SCHEMES),
        link_rel=None,
        strip_comments=True,
    )


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def document_html(draft: DocumentDraft, *, created_on: date) -> str:
    """The whole page. Titles, headings and sources are escaped text; bodies go through
    `markdown_to_html`. The stylesheet is the one bundled file the renderer may load."""
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f"<title>{_esc(draft.title)}</title>",
        f'<link rel="stylesheet" href="{STYLESHEET_URL}">',
        "</head><body>",
        f"<header><h1>{_esc(draft.title)}</h1>",
        f'<p class="meta">Created by Nexus AI on {created_on.isoformat()}</p></header>',
    ]
    for section in draft.sections:
        parts.append(
            f"<section><h2>{_esc(section.heading)}</h2>{markdown_to_html(section.body_md)}</section>"
        )
    if draft.sources:
        parts.append('<section class="sources"><h2>Sources</h2><ol>')
        for source in draft.sources:
            url = source.url
            link = f' <span class="url">{_esc(url)}</span>' if url and _is_web_link(url) else ""
            parts.append(f'<li value="{source.index}">{_esc(source.title)}{link}</li>')
        parts.append("</ol></section>")
    parts.append("</body></html>")
    return "".join(parts)
