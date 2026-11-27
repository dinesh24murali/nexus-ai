from typing import Any

import pytest
from app.llm.redaction import hash_user_id, mask

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("mail me at jane.doe+work@example.co.uk please", "[EMAIL]"),
        ("call +1 (415) 555-0132 tomorrow", "[PHONE]"),
        ("call 415-555-0132 tomorrow", "[PHONE]"),
        ("ring +44 20 7946 0958", "[PHONE]"),
        ("card 4111 1111 1111 1111 expires", "[CARD]"),
        ("card 4111-1111-1111-1111 expires", "[CARD]"),
        ("card 4111111111111111 expires", "[CARD]"),
        ("pay to GB82 WEST 1234 5698 7654 32 now", "[IBAN]"),
        ("pay to DE89370400440532013000 now", "[IBAN]"),
        (f"Authorization: Bearer {JWT}", "[JWT]"),
        ("key sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD", "[API_KEY]"),
        ("key sk-abcdefghijklmnopqrstuvwxyz0123456789", "[API_KEY]"),
        ("pk-lf-0123456789abcdef0123456789abcdef", "[API_KEY]"),
        ("tvly-abcdefghijklmnopqrstuvwxyz012345", "[API_KEY]"),
        ("github ghp_abcdefghijklmnopqrstuvwxyz0123456789", "[API_KEY]"),
        ("aws AKIAIOSFODNN7EXAMPLE", "[API_KEY]"),
    ],
)
def test_sensitive_values_are_replaced(raw: str, label: str) -> None:
    masked = mask(raw)
    assert label in masked
    for secret in (
        "jane.doe",
        "555-0132",
        "555 0132",
        "4111",
        "7946",
        "WEST",
        "0440532013000",
        "eyJhbGci",
        "abcdefghijklmnop",
        "0123456789abcdef",
        "IOSFODNN7",
    ):
        if secret in raw:
            assert secret not in masked


def test_plain_text_and_ordinary_numbers_are_untouched() -> None:
    text = (
        "In 2026 we shipped 3 releases, v1.2.3, order #12345, ratio 0.75, "
        "ISO 8601 dates 2026-11-21."
    )
    assert mask(text) == text


def test_mask_recurses_into_containers_and_keeps_shape() -> None:
    data: dict[str, Any] = {
        "input": [{"role": "user", "content": "I am jane@example.com"}],
        "n": 3,
        "ok": True,
        "none": None,
        "nested": ({"deep": "call 415-555-0132"},),
    }
    out = mask(data)
    assert out["input"][0]["content"] == "I am [EMAIL]"
    assert out["n"] == 3
    assert out["ok"] is True
    assert out["none"] is None
    assert out["nested"][0]["deep"] == "call [PHONE]"
    assert data["input"][0]["content"] == "I am jane@example.com"  # input is not mutated


def test_mask_never_raises_on_odd_objects() -> None:
    class Odd:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    odd = Odd()
    assert mask(odd) is odd


def test_mask_handles_cyclic_structures() -> None:
    a: dict[str, object] = {"email": "x@example.com"}
    a["self"] = a
    out = mask(a)
    assert out["email"] == "[EMAIL]"


def test_hash_user_id_is_stable_keyed_and_does_not_leak_the_id() -> None:
    user_id = "6f1c7a62-0000-4000-8000-000000000001"
    first = hash_user_id(user_id, secret="secret-one")
    assert first == hash_user_id(user_id, secret="secret-one")
    assert first != hash_user_id(user_id, secret="secret-two")
    assert first != hash_user_id("someone-else", secret="secret-one")
    assert user_id not in first
    assert len(first) == 32
