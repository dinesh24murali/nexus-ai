"""/me endpoints and the bearer-token dependency."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from app.core.config import Settings
from app.core.security import create_access_token
from fastapi.testclient import TestClient

from .test_auth import API, PASSWORD, login, refresh_cookie, with_cookie

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def signed_in(client: TestClient, make_user: Any) -> tuple[str, str]:
    """-> (user_id, access_token) for a freshly created user."""
    uid = make_user()
    return uid, login(client).json()["access_token"]


def test_me_requires_a_token(client: TestClient) -> None:
    r = client.get(f"{API}/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer garbage", "Basic abc", "Bearer", "garbage"])
def test_me_rejects_malformed_authorization(client: TestClient, header: str) -> None:
    assert client.get(f"{API}/me", headers={"authorization": header}).status_code == 401


def test_me_with_expired_token_says_token_expired(
    client: TestClient, settings: Settings, make_user: Any
) -> None:
    uid = make_user()
    old = create_access_token(
        UUID(uid), uuid4(), settings, now=datetime.now(UTC) - timedelta(hours=2)
    )
    r = client.get(f"{API}/me", headers=bearer(old))
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_me_returns_the_current_user_only(client: TestClient, make_user: Any) -> None:
    make_user(email="other@example.com", display_name="Other")
    uid, token = signed_in(client, make_user)
    r = client.get(f"{API}/me", headers=bearer(token))
    assert r.status_code == 200
    assert r.json()["id"] == uid and r.json()["email"] == "marco@example.com"


def test_token_for_a_deleted_user_is_rejected(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    _, token = signed_in(client, make_user)
    db.execute("DELETE FROM users")
    assert client.get(f"{API}/me", headers=bearer(token)).status_code == 401


def test_valid_token_of_a_disabled_user_is_rejected(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    _, token = signed_in(client, make_user)
    db.execute("UPDATE users SET status = 'disabled'")
    assert client.get(f"{API}/me", headers=bearer(token)).status_code == 401


def test_patch_me_updates_profile_and_memory_toggle(client: TestClient, make_user: Any) -> None:
    _, token = signed_in(client, make_user)
    r = client.patch(
        f"{API}/me",
        headers=bearer(token),
        json={"display_name": "  Marco P.  ", "memory_enabled": False},
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Marco P."
    assert r.json()["memory_enabled"] is False
    assert client.get(f"{API}/me", headers=bearer(token)).json()["memory_enabled"] is False


def test_patch_me_with_empty_body_is_a_noop(client: TestClient, make_user: Any) -> None:
    """All fields are optional in the spec, so `{}` is valid: change nothing, return the user."""
    uid, token = signed_in(client, make_user)
    r = client.patch(f"{API}/me", headers=bearer(token), json={})
    assert r.status_code == 200 and r.json()["id"] == uid
    assert r.json()["display_name"] == "Marco" and r.json()["memory_enabled"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": ""},
        {"display_name": "x" * 61},
        {"memory_enabled": "maybe"},
        # The spec types these as non-nullable: null is a violation, not "leave unchanged".
        {"memory_enabled": None},
        {"display_name": None},
        # Strict booleans: no 0/1/"true" coercion.
        {"memory_enabled": 1},
        {"memory_enabled": "true"},
    ],
)
def test_patch_me_validation(client: TestClient, make_user: Any, body: dict[str, Any]) -> None:
    _, token = signed_in(client, make_user)
    assert client.patch(f"{API}/me", headers=bearer(token), json=body).status_code == 422


def test_patch_me_cannot_change_protected_fields(client: TestClient, make_user: Any) -> None:
    uid, token = signed_in(client, make_user)
    client.patch(
        f"{API}/me",
        headers=bearer(token),
        json={
            "memory_enabled": False,
            "email": "evil@example.com",
            "status": "disabled",
            "id": str(uuid4()),
        },
    )
    me = client.get(f"{API}/me", headers=bearer(token)).json()
    assert me["email"] == "marco@example.com" and me["id"] == uid


# ───────────────────── password change ─────────────────────


def test_change_password_wrong_current_is_400(client: TestClient, make_user: Any) -> None:
    _, token = signed_in(client, make_user)
    r = client.post(
        f"{API}/me/password",
        headers=bearer(token),
        json={"current_password": "not-the-password-1", "new_password": "another-long-passphrase"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PASSWORD"


def test_change_password_validates_new_password(client: TestClient, make_user: Any) -> None:
    _, token = signed_in(client, make_user)
    r = client.post(
        f"{API}/me/password",
        headers=bearer(token),
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert r.status_code == 422


def test_change_password_switches_credentials_and_revokes_other_sessions_only(
    client: TestClient, make_user: Any
) -> None:
    make_user()
    current = login(client)  # the session making the change
    other = login(client)  # another device
    access = current.json()["access_token"]
    r = client.post(
        f"{API}/me/password",
        headers=bearer(access),
        json={"current_password": PASSWORD, "new_password": "another-long-passphrase"},
    )
    assert r.status_code == 204

    assert login(client, password=PASSWORD).status_code == 401  # old password is dead
    assert login(client, password="another-long-passphrase").status_code == 200

    # Other devices are signed out; the session that changed the password survives.
    assert (
        client.post(f"{API}/auth/refresh", headers=with_cookie(refresh_cookie(other))).status_code
        == 401
    )
    assert (
        client.post(f"{API}/auth/refresh", headers=with_cookie(refresh_cookie(current))).status_code
        == 200
    )
