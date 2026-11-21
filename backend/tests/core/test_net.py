from app.core.net import client_ip
from fastapi import Request


def make_request(headers: dict[str, str], peer: str = "10.0.0.5") -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    }
    return Request(scope)


def test_untrusted_proxy_ignores_forwarded_header() -> None:
    req = make_request({"x-forwarded-for": "1.2.3.4"})
    assert client_ip(req, trusted_proxy=False) == "10.0.0.5"


def test_trusted_proxy_uses_the_last_forwarded_entry() -> None:
    # A client can prepend fake entries; only the proxy-appended last one is trustworthy.
    req = make_request({"x-forwarded-for": "6.6.6.6, 203.0.113.9"})
    assert client_ip(req, trusted_proxy=True) == "203.0.113.9"


def test_trusted_proxy_without_header_falls_back_to_peer() -> None:
    assert client_ip(make_request({}), trusted_proxy=True) == "10.0.0.5"
