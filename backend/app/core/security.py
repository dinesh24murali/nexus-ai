"""Password hashing, access tokens (JWT), and opaque token helpers.

* Passwords: argon2id (argon2-cffi defaults).
* Access token: JWT HS256, 15 minutes, claims `sub` (user id), `fid` (refresh-token family id, so
  "change password" can keep the caller's own session), `iat`, `exp`. Kept in client memory only.
* Refresh and invite tokens are opaque random strings; only their SHA-256 hash is stored.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings

ACCESS_TOKEN_TTL_S = 15 * 60
REFRESH_TOKEN_TTL_S = 7 * 24 * 3600
_JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()  # argon2id


class TokenExpiredError(Exception):
    """The access token's signature is fine but `exp` has passed (client should refresh)."""


class InvalidTokenError(Exception):
    """Malformed, wrongly signed, wrong algorithm, or missing required claims."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    family_id: UUID


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def create_access_token(
    user_id: UUID, family_id: UUID, settings: Settings, *, now: datetime | None = None
) -> str:
    issued = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "fid": str(family_id),
        "iat": issued,
        "exp": issued + timedelta(seconds=ACCESS_TOKEN_TTL_S),
    }
    return jwt.encode(payload, settings.auth_secret.get_secret_value(), algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret.get_secret_value(),
            algorithms=[_JWT_ALGORITHM],  # never trust the token's own `alg` header
            options={"require": ["exp", "iat", "sub", "fid"]},
        )
        return AccessClaims(user_id=UUID(payload["sub"]), family_id=UUID(payload["fid"]))
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError from exc
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError from exc


def generate_invite_code() -> str:
    return f"nx_{secrets.token_hex(16)}"  # 128 bits; 35 chars (spec: 16..64)


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
