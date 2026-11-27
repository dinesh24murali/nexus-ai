from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from app.core.config import Settings
from app.core.security import (
    ACCESS_TOKEN_TTL_S,
    InvalidTokenError,
    TokenExpiredError,
    create_access_token,
    decode_access_token,
    generate_invite_code,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_is_argon2id_and_verifies() -> None:
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password("correct-horse-battery", h)
    assert not verify_password("wrong-password-here", h)


def test_password_hashes_are_salted() -> None:
    assert hash_password("same-password-123") != hash_password("same-password-123")


def test_verify_password_never_raises_on_garbage_hash() -> None:
    assert verify_password("x", "not-a-hash") is False


def test_access_token_round_trip(settings: Settings) -> None:
    uid, fid = uuid4(), uuid4()
    token = create_access_token(uid, fid, settings)
    claims = decode_access_token(token, settings)
    assert claims.user_id == uid
    assert claims.family_id == fid


def test_access_token_lifetime_is_15_minutes(settings: Settings) -> None:
    assert ACCESS_TOKEN_TTL_S == 900
    token = create_access_token(uuid4(), uuid4(), settings)
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["exp"] - payload["iat"] == 900


def test_expired_token_raises_expired(settings: Settings) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    token = create_access_token(uuid4(), uuid4(), settings, now=long_ago)
    with pytest.raises(TokenExpiredError):
        decode_access_token(token, settings)


def test_token_signed_with_other_secret_rejected(settings: Settings) -> None:
    other = settings.model_copy(update={"auth_secret": type(settings.auth_secret)("z" * 40)})
    token = create_access_token(uuid4(), uuid4(), other)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_alg_none_token_rejected(settings: Settings) -> None:
    forged = jwt.encode(
        {"sub": str(uuid4()), "fid": str(uuid4()), "exp": datetime.now(UTC) + timedelta(hours=1)},
        key="",
        algorithm="none",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged, settings)


def test_token_missing_claims_rejected(settings: Settings) -> None:
    secret = settings.auth_secret.get_secret_value()
    bare = jwt.encode({"exp": datetime.now(UTC) + timedelta(minutes=5)}, secret, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_access_token(bare, settings)


def test_garbage_token_rejected(settings: Settings) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token("not.a.jwt", settings)


def test_invite_codes_are_unique_prefixed_and_long_enough() -> None:
    codes = {generate_invite_code() for _ in range(50)}
    assert len(codes) == 50
    for c in codes:
        assert c.startswith("nx_")
        assert 16 <= len(c) <= 64  # spec: minLength 16, maxLength 64


def test_refresh_tokens_are_high_entropy_and_unique() -> None:
    tokens = {generate_refresh_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 43 for t in tokens)  # >= 256 bits url-safe


def test_hash_token_is_sha256_hex_and_deterministic() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert len(hash_token("abc")) == 64
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
