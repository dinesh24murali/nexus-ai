import pytest
from app.api.schemas import LoginRequest, RegisterRequest
from pydantic import ValidationError

CODE = "nx_" + "a" * 32


def register(email: str) -> RegisterRequest:
    return RegisterRequest(
        invite_code=CODE, email=email, password="correct-horse-battery", display_name="Marco"
    )


@pytest.mark.parametrize(
    "email",
    [
        "marco@example.com",
        "Marco.P+tag@Example.COM",
        "a@b.test",  # reserved/special-use names are fine: the address is only a login identifier
        "a@b.invalid",
        "a@localhost",
        "weird!#$%&'*+/=?^_`{|}~-@x.y",
    ],
)
def test_email_accepts_any_structurally_valid_address_and_lowercases_it(email: str) -> None:
    assert register(email).email == email.strip().lower()
    assert LoginRequest(email=email, password="x").email == email.strip().lower()


@pytest.mark.parametrize(
    "email",
    [
        "",
        "no-at-sign",
        "@x.y",
        "a@",
        "a b@c.d",
        "a@b c.d",
        "a@@b.c",
        "a@b@c.d",
        "a\n@b.c",
        "a" * 250 + "@b.cd",
    ],
)
def test_email_rejects_malformed_addresses(email: str) -> None:
    with pytest.raises(ValidationError):
        register(email)
    with pytest.raises(ValidationError):
        LoginRequest(email=email, password="x")


def test_email_is_trimmed() -> None:
    assert register("  marco@example.com  ").email == "marco@example.com"
