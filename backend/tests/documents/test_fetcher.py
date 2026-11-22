"""The renderer's URL fetcher serves bundled files only (doc 10 LLM-T18)."""

import pytest
from app.documents.bundled import BUNDLED, STYLESHEET_URL
from app.documents.fetcher import DenyAllFetcher, FetchDenied, scheme_of

REFUSED = [
    "file:///etc/passwd",
    "FILE:///etc/passwd",
    "file://localhost/etc/hostname",
    "http://169.254.169.254/latest/meta-data",
    "https://example.com/logo.png",
    "HTTP://EXAMPLE.COM/x",
    "ftp://example.com/file",
    "data:text/css,body{background:url(http://evil.test/x)}",
    "data:image/png;base64,AAAA",
    "gopher://example.com/",
    "javascript:alert(1)",
    "//example.com/x.css",
    "relative/path.css",
    "nexus-bundled:../../etc/passwd",
    "nexus-bundled:document.css?x=1",
    "nexus-bundled:other.css",
    "NEXUS-BUNDLED:document.css",
    "",
]


@pytest.mark.parametrize("url", REFUSED)
def test_everything_but_a_bundled_name_is_refused(url: str) -> None:
    fetcher = DenyAllFetcher()
    with pytest.raises(FetchDenied):
        fetcher.fetch(url)
    assert fetcher.denied_count == 1
    assert fetcher.served == 0


def test_a_refusal_does_not_carry_the_url() -> None:
    fetcher = DenyAllFetcher()
    with pytest.raises(FetchDenied) as raised:
        fetcher.fetch("https://user:s3cret@evil.test/path?token=abc")
    assert "s3cret" not in str(raised.value)
    assert "evil.test" not in str(raised.value)
    # Only a count by scheme is kept, never the address.
    assert dict(fetcher.denied) == {"https": 1}


def test_the_stylesheet_is_served() -> None:
    fetcher = DenyAllFetcher()
    response = fetcher.fetch(STYLESHEET_URL)
    assert response.content_type == "text/css"
    assert b"@page" in response.read()
    assert fetcher.served == 1
    assert fetcher.denied_count == 0


def test_the_allowlist_is_only_the_stylesheet() -> None:
    # Adding to the allowlist is a deliberate change that has to touch this test.
    assert set(BUNDLED) == {STYLESHEET_URL}


@pytest.mark.parametrize(
    ("url", "scheme"),
    [("https://x", "https"), ("FILE:///x", "file"), ("data:,x", "data"), ("rel/x", "relative")],
)
def test_scheme_of(url: str, scheme: str) -> None:
    assert scheme_of(url) == scheme
