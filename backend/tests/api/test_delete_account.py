"""`DELETE /me` (TASK-028): the request side of account deletion. The erasure is the `purge_user`
job (tests/jobs/test_purge_user.py)."""

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .test_auth import API, PASSWORD, login, refresh_cookie, with_cookie
from .test_me import bearer, signed_in

pytestmark = pytest.mark.integration

EMAIL = "marco@example.com"


def delete_me(client: TestClient, token: str, email: str = EMAIL) -> Any:
    return client.request(
        "DELETE", f"{API}/me", headers=bearer(token), json={"confirm_email": email}
    )


def status_of(db: psycopg.Connection[Any], uid: str) -> str:
    row = db.execute("SELECT status FROM users WHERE id = %s", (uid,)).fetchone()
    assert row
    return str(row[0])


def purge_jobs(db: psycopg.Connection[Any]) -> list[tuple[str, str]]:
    rows = db.execute(
        "SELECT status, idempotency_key FROM jobs WHERE kind = 'purge_user'"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def test_delete_requires_a_token(client: TestClient) -> None:
    r = client.request("DELETE", f"{API}/me", json={"confirm_email": EMAIL})
    assert r.status_code == 401


def test_a_matching_email_schedules_the_purge(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    uid, token = signed_in(client, make_user)

    r = delete_me(client, token)

    assert r.status_code == 202 and r.content == b""
    assert status_of(db, uid) == "pending_deletion"
    assert purge_jobs(db) == [("queued", f"purge:{uid}")]


def test_the_confirmation_ignores_case_and_surrounding_space(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    uid, token = signed_in(client, make_user)

    assert delete_me(client, token, "  Marco@Example.COM ").status_code == 202
    assert status_of(db, uid) == "pending_deletion"


@pytest.mark.parametrize("typed", ["someone@else.com", "marco@example.org"])
def test_a_wrong_email_changes_nothing(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], typed: str
) -> None:
    uid, token = signed_in(client, make_user)

    r = delete_me(client, token, typed)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CONFIRMATION_MISMATCH"
    assert typed not in r.text  # what was typed is never echoed
    assert status_of(db, uid) == "active"
    assert purge_jobs(db) == []
    assert client.get(f"{API}/me", headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("body", [{}, {"confirm_email": "not an email"}, {"confirm_email": None}])
def test_a_malformed_body_is_a_validation_error(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any], body: dict[str, Any]
) -> None:
    uid, token = signed_in(client, make_user)

    r = client.request("DELETE", f"{API}/me", headers=bearer(token), json=body)

    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert status_of(db, uid) == "active"


def test_every_session_ends_at_once(client: TestClient, make_user: Any) -> None:
    make_user()
    first = login(client)
    second = login(client)  # a second device
    token = first.json()["access_token"]

    assert delete_me(client, token).status_code == 202

    # The access token stops working at once (the account is no longer active) ...
    assert client.get(f"{API}/me", headers=bearer(token)).status_code == 401
    # ... and neither refresh cookie can mint another.
    for resp in (first, second):
        r = client.post(f"{API}/auth/refresh", headers=with_cookie(refresh_cookie(resp)))
        assert r.status_code == 401


def test_a_user_waiting_for_deletion_cannot_sign_in(client: TestClient, make_user: Any) -> None:
    _, token = signed_in(client, make_user)
    delete_me(client, token)

    r = login(client)

    assert r.status_code == 401  # the same answer as a wrong password: nothing is revealed
    assert (
        r.json()["error"]["code"] == login(client, password=PASSWORD + "x").json()["error"]["code"]
    )


def test_asking_twice_queues_one_purge(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    uid, token = signed_in(client, make_user)
    delete_me(client, token)
    # A second request cannot be authenticated any more; the queue's key also makes a replay safe.
    assert delete_me(client, token).status_code == 401
    assert purge_jobs(db) == [("queued", f"purge:{uid}")]


def test_work_that_has_not_started_is_cancelled(
    client: TestClient, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    uid, token = signed_in(client, make_user)
    other = make_user(email="other@example.com")
    for owner, key in ((uid, "mine"), (other, "theirs")):
        db.execute(
            "INSERT INTO jobs (kind, payload, idempotency_key, user_id)"
            " VALUES ('generate_title', '{}', %s, %s)",
            (key, owner),
        )

    delete_me(client, token)

    rows = dict(
        db.execute(
            "SELECT idempotency_key, status FROM jobs WHERE kind = 'generate_title'"
        ).fetchall()
    )
    assert rows == {"mine": "cancelled", "theirs": "queued"}
