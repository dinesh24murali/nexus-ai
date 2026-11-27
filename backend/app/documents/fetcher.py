"""The URL fetcher the PDF renderer uses: it serves bundled files and nothing else (LLM-T18).

A document is built from untrusted text (a conversation, web results, a model's draft). Even after
the Markdown is sanitised, the renderer must not be able to read a local file or call a URL if
some markup got through, so the fetcher answers only for the names in `BUNDLED` and refuses every
other request, whatever its scheme (`file:`, `http(s):`, `ftp:`, `data:` ...). Each refusal is
counted: the count is the `documents.external_fetches` metric, whose gate is zero, so a document
that even *tried* to reach out is visible. Refused URLs are never kept or logged (they come from
untrusted content and may carry secrets); only a count by scheme is.
"""

from collections import Counter

from weasyprint.urls import URLFetcher, URLFetcherResponse, URLFetchingError

from app.documents.bundled import ASSETS_DIR, BUNDLED


class FetchDenied(URLFetchingError):
    """A request for something that is not bundled."""


def scheme_of(url: str) -> str:
    head, sep, _ = url.partition(":")
    return head.lower()[:16] if sep and head.isascii() and head.isalnum() else "relative"


class DenyAllFetcher(URLFetcher):
    """`url_fetcher` for `weasyprint.HTML`: bundled files only, everything else refused and counted.

    It subclasses WeasyPrint's `URLFetcher` because the library reads settings from that class, but
    `fetch` never calls the parent: no HTTP, file or data handler is ever reached."""

    def __init__(self) -> None:
        super().__init__(allowed_protocols=(), fail_on_errors=False)
        self.served = 0
        self.denied: Counter[str] = Counter()  # by scheme only

    @property
    def denied_count(self) -> int:
        return sum(self.denied.values())

    def fetch(self, url: str, headers: object = None) -> URLFetcherResponse:
        entry = BUNDLED.get(url)
        if entry is None:
            self.denied[scheme_of(url)] += 1
            raise FetchDenied("resource not available")  # no URL in the message
        name, media_type = entry
        self.served += 1
        return URLFetcherResponse(
            url, body=(ASSETS_DIR / name).read_bytes(), headers={"Content-Type": media_type}
        )
