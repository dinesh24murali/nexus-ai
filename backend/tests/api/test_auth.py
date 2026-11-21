"""Auth endpoints against a real Postgres: register (invite-gated), login, refresh, logout."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery"
API = "/api/v1"


def register_body(code: str | None, email: str = "marco@example.com") -> dict[str, Any]:
    body: dict[str, Any] = {"email": email, "password": PASSWORD, "display_name": "Marco"}
    if code is not None:
        body["invite_code"] = code
    return body


def refresh_cookie(resp: Any) -> str:
    """The raw `nexus_refresh` value from a response's Set-Cookie header."""
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith("nexus_refresh="):
            return str(header.split(";", 1)[0].split("=", 1)[1])
    raise AssertionError("no nexus_refresh cookie in response")


def set_cookie_header(resp: Any) -> str:
    return str(
        next(h for h in resp.headers.get_list("set-cookie") if h.startswith("nexus_refresh="))
    )


def with_cookie(token: str) -> dict[str, str]:
    return {"cookie": f"nexus_refresh={token}"}


def login(client: TestClient, email: str = "marco@example.com", password: str = PASSWORD) -> Any:
    client.cookies.clear()
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


# ───────────────────────────── register ─────────────────────────────


def test_register_success_returns_tokens_user_and_hardened_cookie(
    client: TestClient, make_invite: Any
) -> None:
    r = client.post(f"{API}/auth/register", json=register_body(make_invite()))
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["access_token"]
    user = body["user"]
    assert set(user) == {"id", "email", "display_name", "memory_enabled", "created_at"}
    assert user["email"] == "marco@example.com"
    assert user["memory_enabled"] is True
    assert "password" not in r.text and "hash" not in r.text

    cookie = set_cookie_header(r).lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api/v1/auth" in cookie
    assert "max-age=604800" in cookie


def test_register_stores_only_hashes(
    client: TestClient, make_invite: Any, db: psycopg.Connection[Any]
) -> None:
    code = make_invite()
    r = client.post(f"{API}/auth/register", json=register_body(code))
    refresh = refresh_cookie(r)
    (pw_hash,) = db.execute("SELECT password_hash FROM users").fetchone() or (None,)
    assert pw_hash and pw_hash.startswith("$argon2id$") and PASSWORD not in pw_hash
    stored = [row[0] for row in db.execute("SELECT token_hash FROM refresh_tokens").fetchall()]
    assert stored and refresh not in stored
    invites = [row[0] for row in db.execute("SELECT code_hash FROM invites").fetchall()]
    assert code not in invites


def test_invite_is_single_use(client: TestClient, make_invite: Any) -> None:
    code = make_invite()
    assert client.post(f"{API}/auth/register", json=register_body(code)).status_code == 201
    second = client.post(
        f"{API}/auth/register", json=register_body(code, email="other@example.com")
    )
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "INVITE_INVALID"


def test_invite_records_who_used_it(
    client: TestClient, make_invite: Any, db: psycopg.Connection[Any]
) -> None:
    make_invite()
    code = make_invite(note="Priya")
    r = client.post(f"{API}/auth/register", json=register_body(code))
    row = db.execute("SELECT used_by, used_at FROM invites WHERE note = 'Priya'").fetchone()
    assert row and str(row[0]) == r.json()["user"]["id"] and row[1] is not None


@pytest.mark.parametrize("kind", ["unknown", "used", "revoked", "expired", "missing"])
def test_every_invalid_invite_gets_the_identical_response(
    client: TestClient, make_invite: Any, kind: str
) -> None:
    """INVITE_INVALID must not reveal *why* a code failed (unknown/used/revoked/expired)."""
    codes: dict[str, str | None] = {
        "unknown": "nx_" + "0" * 32,
        "used": make_invite(used=True),
        "revoked": make_invite(revoked=True),
        "expired": make_invite(expires_in=timedelta(days=-1)),
        "missing": None,
    }
    r = client.post(f"{API}/auth/register", json=register_body(codes[kind]))
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "INVITE_INVALID"
    assert err["message"] == "This invite code is invalid, used or expired."
    assert "details" not in err


def test_malformed_invite_code_is_a_422_per_the_spec(client: TestClient) -> None:
    r = client.post(f"{API}/auth/register", json=register_body("nx_short"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_bad_invite_does_not_reveal_whether_email_exists(
    client: TestClient, make_invite: Any, make_user: Any
) -> None:
    make_user(email="taken@example.com")
    r = client.post(
        f"{API}/auth/register", json=register_body("nx_" + "1" * 32, email="taken@example.com")
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "INVITE_INVALID"


def test_duplicate_email_is_409_and_does_not_burn_the_invite(
    client: TestClient, make_invite: Any, make_user: Any
) -> None:
    make_user(email="marco@example.com")
    code = make_invite()
    r = client.post(f"{API}/auth/register", json=register_body(code, email="MARCO@example.com"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_TAKEN"
    retry = client.post(f"{API}/auth/register", json=register_body(code, email="new@example.com"))
    assert retry.status_code == 201, "a failed registration must not consume the invite"


def test_concurrent_registrations_with_one_invite_create_exactly_one_user(
    client: TestClient, make_invite: Any, db: psycopg.Connection[Any]
) -> None:
    code = make_invite()

    def attempt(i: int) -> int:
        return client.post(
            f"{API}/auth/register", json=register_body(code, email=f"u{i}@example.com")
        ).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(attempt, range(6)))
    assert statuses.count(201) == 1
    assert statuses.count(403) == 5
    assert db.execute("SELECT count(*) FROM users").fetchone() == (1,)


def test_open_registration_mode_needs_no_invite(settings: Settings, db_url: str) -> None:
    app = create_app(
        settings.model_copy(update={"database_url": db_url, "registration_mode": "open"})
    )
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post(f"{API}/auth/register", json=register_body(None)).status_code == 201


@pytest.mark.parametrize(
    "patch",
    [
        {"password": "short"},
        {"password": "x" * 129},
        {"email": "not-an-email"},
        {"display_name": ""},
        {"display_name": "x" * 61},
    ],
)
def test_register_validation_errors_are_422(
    client: TestClient, make_invite: Any, patch: dict[str, Any]
) -> None:
    r = client.post(f"{API}/auth/register", json=register_body(make_invite()) | patch)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert PASSWORD not in r.text


def test_register_attempts_with_bad_invites_are_rate_limited(client: TestClient) -> None:
    """Invite guessing is throttled per IP: 10 bad attempts, then 429 even for a good code."""
    for _ in range(10):
        assert (
            client.post(f"{API}/auth/register", json=register_body("nx_" + "2" * 32)).status_code
            == 403
        )
    r = client.post(f"{API}/auth/register", json=register_body("nx_" + "2" * 32))
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert int(r.headers["retry-after"]) > 0


# ───────────────────────────── login ─────────────────────────────


def test_login_success(client: TestClient, make_user: Any) -> None:
    make_user()
    r = login(client)
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "marco@example.com"
    assert refresh_cookie(r)


def test_login_email_is_case_insensitive(client: TestClient, make_user: Any) -> None:
    make_user(email="Marco@Example.com")
    assert login(client, email="marco@EXAMPLE.com").status_code == 200


def test_login_failures_are_indistinguishable(client: TestClient, make_user: Any) -> None:
    """Wrong password and unknown email return the same status, code and message."""
    make_user()
    wrong_pw = login(client, password="not-the-password-1")
    no_user = login(client, email="ghost@example.com", password="not-the-password-1")
    assert wrong_pw.status_code == no_user.status_code == 401

    def strip(r: Any) -> dict[str, Any]:
        return {k: v for k, v in r.json()["error"].items() if k != "request_id"}

    assert strip(wrong_pw) == strip(no_user)
    assert wrong_pw.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_disabled_account_is_revealed_only_after_a_correct_password(
    client: TestClient, make_user: Any
) -> None:
    make_user(status="disabled")
    wrong = login(client, password="not-the-password-1")
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
    right = login(client)
    assert right.status_code == 403 and right.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_login_is_rate_limited_per_ip_and_email_after_10_failures(
    client: TestClient, make_user: Any
) -> None:
    make_user()
    for _ in range(10):
        assert login(client, password="not-the-password-1").status_code == 401
    blocked = login(client, password="not-the-password-1")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) > 0
    # The correct password is blocked too while the window is hot, ...
    assert login(client).status_code == 429
    # ... but a different email from the same IP is unaffected.
    assert login(client, email="ghost@example.com", password="x" * 12).status_code == 401


# ───────────────────────────── refresh ─────────────────────────────


def test_refresh_rotates_the_token(client: TestClient, make_user: Any) -> None:
    make_user()
    first = refresh_cookie(login(client))
    r = client.post(f"{API}/auth/refresh", headers=with_cookie(first))
    assert r.status_code == 200
    assert r.json()["access_token"]
    assert refresh_cookie(r) != first


def test_refresh_token_reuse_revokes_the_whole_family(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    make_user()
    first = refresh_cookie(login(client))
    second = refresh_cookie(client.post(f"{API}/auth/refresh", headers=with_cookie(first)))

    # An attacker (or a buggy client) replays the already-rotated token...
    replay = client.post(f"{API}/auth/refresh", headers=with_cookie(first))
    assert replay.status_code == 401

    # ...which kills the legitimate descendant too: every token in the family is revoked.
    after = client.post(f"{API}/auth/refresh", headers=with_cookie(second))
    assert after.status_code == 401
    assert db.execute(
        "SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL"
    ).fetchone() == (0,)


def test_reuse_only_revokes_its_own_family(client: TestClient, make_user: Any) -> None:
    make_user()
    a1 = refresh_cookie(login(client))
    b1 = refresh_cookie(login(client))  # a second, independent session
    a2 = refresh_cookie(client.post(f"{API}/auth/refresh", headers=with_cookie(a1)))
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(a1)).status_code == 401
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(a2)).status_code == 401
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(b1)).status_code == 200


def test_refresh_without_or_with_garbage_cookie_is_401(client: TestClient) -> None:
    assert client.post(f"{API}/auth/refresh").status_code == 401
    assert client.post(f"{API}/auth/refresh", headers=with_cookie("garbage")).status_code == 401


def test_expired_refresh_token_is_401(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    make_user()
    token = refresh_cookie(login(client))
    db.execute("UPDATE refresh_tokens SET expires_at = now() - interval '1 minute'")
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(token)).status_code == 401


def test_refresh_for_disabled_user_is_401(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    make_user()
    token = refresh_cookie(login(client))
    db.execute("UPDATE users SET status = 'disabled'")
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(token)).status_code == 401


def test_concurrent_refresh_with_one_token_succeeds_at_most_once(
    client: TestClient, make_user: Any
) -> None:
    make_user()
    token = refresh_cookie(login(client))

    def attempt(_: int) -> int:
        return client.post(f"{API}/auth/refresh", headers=with_cookie(token)).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(attempt, range(6)))
    assert statuses.count(200) <= 1


# ───────────────────────────── logout ─────────────────────────────


def test_logout_revokes_the_session_and_clears_the_cookie(
    client: TestClient, make_user: Any
) -> None:
    make_user()
    r = login(client)
    access, token = r.json()["access_token"], refresh_cookie(r)
    out = client.post(
        f"{API}/auth/logout",
        headers={"authorization": f"Bearer {access}"} | with_cookie(token),
    )
    assert out.status_code == 204
    assert "max-age=0" in set_cookie_header(out).lower()
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(token)).status_code == 401


def test_logout_requires_authentication(client: TestClient) -> None:
    assert client.post(f"{API}/auth/logout").status_code == 401
