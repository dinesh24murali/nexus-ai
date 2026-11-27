"""Untrusted Markdown becomes safe HTML (doc 10 LLM-T18)."""

import re
from datetime import date
from html.parser import HTMLParser

import pytest
from app.documents.draft import DocumentDraft, Section, Source
from app.documents.markup import ALLOWED_TAGS, document_html, markdown_to_html

HOSTILE = [
    '<img src="file:///etc/passwd">',
    "![x](http://169.254.169.254/latest/meta-data)",
    "![x](file:///etc/passwd)",
    '<a href="javascript:alert(1)">click</a>',
    "[click](javascript:alert(1))",
    "[click](JaVaScRiPt:alert(1))",
    "[click](data:text/html;base64,PHNjcmlwdD4=)",
    "[click](file:///etc/passwd)",
    "[click](ftp://example.com/x)",
    "[click](//example.com/x)",
    "[click](mailto:someone@example.com)",
    "<script>alert(1)</script>",
    '<link rel="stylesheet" href="http://evil.test/x.css">',
    '<style>@import url("http://evil.test/x.css");</style>',
    '<iframe src="http://evil.test"></iframe>',
    '<svg onload="alert(1)"></svg>',
    '<div style="background:url(http://evil.test/x)">x</div>',
    "<object data='http://evil.test/x'></object>",
]


class _Elements(HTMLParser):
    """What a browser or renderer would see as elements: real tags and attributes, not text that
    merely looks like them (an escaped `&lt;img&gt;` is text)."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.attributes: list[tuple[str, str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        self.attributes.extend((tag, name, value) for name, value in attrs)


def _elements(out: str) -> _Elements:
    parsed = _Elements()
    parsed.feed(out)
    return parsed


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_markdown_leaves_no_active_markup(text: str) -> None:
    seen = _elements(markdown_to_html(text))
    assert seen.tags <= ALLOWED_TAGS, seen.tags
    # The only attribute that survives is `href` on a link, and it is a web address.
    assert all(
        (tag, name) == ("a", "href") and (value or "").startswith(("http://", "https://"))
        for tag, name, value in seen.attributes
    ), seen.attributes


def test_raw_html_is_shown_as_text() -> None:
    out = markdown_to_html('<img src="file:///etc/passwd"> and <b>bold</b>')
    assert "&lt;img" in out
    assert "<img" not in out
    assert "<b>" not in out


def test_an_image_is_never_an_image() -> None:
    out = markdown_to_html("![a picture](http://example.com/p.png)")
    assert "<img" not in out
    # What is left is the address, printed, as a plain web link.
    assert "http://example.com/p.png" in out


def test_a_web_link_keeps_its_address_visible() -> None:
    out = markdown_to_html("[the docs](https://docs.example.org/guide)")
    assert '<a href="https://docs.example.org/guide">the docs</a>' in out
    assert "(https://docs.example.org/guide)" in out


def test_other_links_keep_the_label_only() -> None:
    out = markdown_to_html("[click here](javascript:alert(1)) and [mail](mailto:a@b.c)")
    assert "<a" not in out
    assert "click here" in out
    assert "mail" in out


def test_a_link_address_never_contains_whitespace() -> None:
    # The Markdown parser percent-encodes it; one that still had a space would be dropped.
    seen = _elements(markdown_to_html("[x](<http://example.com/a b>)"))
    assert all(not re.search(r"\s", value or "") for _, _, value in seen.attributes)


def test_headings_inside_a_section_sit_below_the_section_heading() -> None:
    out = markdown_to_html("# One\n\n## Two\n\n###### Six")
    assert "<h3>One</h3>" in out
    assert "<h4>Two</h4>" in out
    assert "<h6>Six</h6>" in out
    assert "<h1" not in out
    assert "<h2" not in out


def test_tables_and_code_survive() -> None:
    out = markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nx < y\n```")
    assert "<table>" in out
    assert "x &lt; y" in out


def _draft(**over: object) -> DocumentDraft:
    base = DocumentDraft(
        title="Q3 <review> & plan",
        sections=[Section(heading="Findings <b>", body_md="Churn fell [1].")],
        sources=[
            Source(index=1, title="Report <x>", url="https://example.com/r?a=1&b=2"),
            Source(index=2, title="Local", url="file:///etc/passwd"),
        ],
    )
    return base.model_copy(update=over)


def test_the_page_escapes_titles_headings_and_sources() -> None:
    page = document_html(_draft(), created_on=date(2026, 11, 22))
    assert "<title>Q3 &lt;review&gt; &amp; plan</title>" in page
    assert "<h2>Findings &lt;b&gt;</h2>" in page
    assert "Report &lt;x&gt;" in page
    assert "<h1>Q3 &lt;review&gt; &amp; plan</h1>" in page


def test_the_page_prints_web_sources_in_full_and_never_a_file_source() -> None:
    page = document_html(_draft(), created_on=date(2026, 11, 22))
    assert "https://example.com/r?a=1&amp;b=2" in page
    assert "file:///etc/passwd" not in page
    assert '<li value="2">Local</li>' in page


def test_the_page_loads_only_the_bundled_stylesheet() -> None:
    page = document_html(_draft(), created_on=date(2026, 11, 22))
    assert re.findall(r'(?:href|src)="([^"]*)"', page) == ["nexus-bundled:document.css"]


def test_an_unparseable_link_is_reduced_to_its_text() -> None:
    out = markdown_to_html("[x](http://[::1)")
    assert "<a" not in out and "x" in out
