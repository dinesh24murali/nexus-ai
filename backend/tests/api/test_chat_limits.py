"""Moderation, the message rate limit and the daily budgets on the chat endpoints (TASK-014)."""

from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

from .test_auth import API
from .test_chat import frames, new_conversation, send
from .test_conversations import auth, user_id

pytestmark = pytest.mark.integration

TODAY = datetime.now(UTC).date()


def make_client(settings: Settings, db_url: str, **limits: Any) -> Generator[TestClient]:
    app = create_app(settings.model_copy(update={"database_url": db_url, **limits}))
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def tight(settings: Settings, db_url: str) -> Generator[TestClient]:
    """A client whose limits are small enough to hit: 3 sends/min and a $0.50 / $5 budget."""
    yield from make_client(settings, db_url, chat_rate_limit_per_min=3)


@pytest.fixture
def chat(tight: TestClient, make_user: Any) -> tuple[TestClient, dict[str, str], str]:
    headers = auth(tight, make_user)
    return tight, headers, new_conversation(tight, headers)


def spend(db: psycopg.Connection[Any], user: str, cost: str) -> None:
    db.execute(
        "INSERT INTO usage_daily (user_id, day, cost_usd) VALUES (%s, %s, %s)"
        " ON CONFLICT (user_id, day) DO UPDATE SET cost_usd = usage_daily.cost_usd + %s",
        (user, TODAY, Decimal(cost), Decimal(cost)),
    )


def count(db: psycopg.Connection[Any], sql: str) -> int:
    row = db.execute(sql).fetchone()
    assert row
    return int(row[0])


# ---------------------------------------------------------------- moderation


def test_flagged_text_is_refused_and_never_stored(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat

    r = send(client, headers, cid, "something [[moderation:flag]] awful")

    assert r.status_code == 400
    error = r.json()["error"]
    assert error["code"] == "MODERATION_FLAGGED"
    assert "awful" not in r.text  # the submitted text is never echoed
    assert count(db, "SELECT count(*) FROM messages") == 0
    assert count(db, "SELECT count(*) FROM conversations WHERE active_run_id IS NOT NULL") == 0
    assert count(db, "SELECT count(*) FROM usage_daily") == 0


def test_after_a_refusal_the_conversation_is_still_usable(
    chat: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "[[moderation:flag]]")

    ok = send(client, headers, cid, "a perfectly fine message")

    assert ok.status_code == 200
    assert frames(ok.text)[-1]["event"] == "done"


def test_when_moderation_is_down_the_message_goes_through(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat

    r = send(client, headers, cid, "hello [[moderation:down]]")

    assert r.status_code == 200
    assert frames(r.text)[-1]["event"] == "done"
    assert count(db, "SELECT count(*) FROM messages WHERE role = 'user'") == 1


# ---------------------------------------------------------------- rate limit


def test_the_fourth_send_in_a_minute_is_rate_limited(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat
    for i in range(3):
        assert send(client, headers, cid, f"message {i}").status_code == 200

    r = send(client, headers, cid, "one too many")

    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert count(db, "SELECT count(*) FROM messages WHERE role = 'user'") == 3  # not stored


def test_regenerating_counts_toward_the_same_limit(
    chat: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "one")
    for _ in range(2):
        assert (
            client.post(f"{API}/conversations/{cid}/regenerate", headers=headers).status_code == 200
        )

    r = client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)

    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"


def test_one_users_messages_do_not_use_up_anothers_limit(
    chat: tuple[TestClient, dict[str, str], str], make_user: Any
) -> None:
    client, headers, cid = chat
    for i in range(3):
        send(client, headers, cid, f"m{i}")
    other = auth(client, make_user, email="other@example.com")
    other_cid = new_conversation(client, other)

    assert send(client, other, other_cid, "hi").status_code == 200


# ---------------------------------------------------------------- budgets


def test_a_user_over_budget_gets_429_with_scope_user(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat
    spend(db, user_id(db), "0.50")

    r = send(client, headers, cid, "hello")

    assert r.status_code == 429
    error = r.json()["error"]
    assert error["code"] == "BUDGET_EXCEEDED"
    assert error["details"]["scope"] == "user"
    assert error["details"]["resets_at"].endswith("T00:00:00Z")
    assert int(r.headers["retry-after"]) >= 1
    assert count(db, "SELECT count(*) FROM messages") == 0
    assert count(db, "SELECT count(*) FROM conversations WHERE active_run_id IS NOT NULL") == 0


def test_the_global_cap_blocks_a_user_who_has_spent_nothing(
    chat: tuple[TestClient, dict[str, str], str],
    db: psycopg.Connection[Any],
    make_user: Any,
) -> None:
    client, headers, cid = chat
    spend(db, make_user("whale@example.com"), "5.00")

    r = send(client, headers, cid, "hello")

    assert r.status_code == 429
    error = r.json()["error"]
    assert (error["code"], error["details"]["scope"]) == ("BUDGET_EXCEEDED", "global")


def test_regenerate_is_blocked_by_the_budget_too(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "hello")
    spend(db, user_id(db), "0.50")

    r = client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)

    assert r.status_code == 429
    assert r.json()["error"]["details"]["scope"] == "user"


def test_cancel_is_never_blocked_by_a_budget(
    chat: tuple[TestClient, dict[str, str], str], db: psycopg.Connection[Any]
) -> None:
    client, headers, cid = chat
    spend(db, user_id(db), "9")

    assert client.post(f"{API}/conversations/{cid}/runs/cancel", headers=headers).status_code == 202


def test_a_finished_run_adds_its_cost_so_the_budget_closes_as_you_spend(
    settings: Settings, db_url: str, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    # A budget of one hundredth of a cent: the first answer overspends it, the second is refused.
    for client in make_client(settings, db_url, user_daily_budget_usd=Decimal("0.0001")):
        headers = auth(client, make_user)
        cid = new_conversation(client, headers)

        assert send(client, headers, cid, "first").status_code == 200
        second = send(client, headers, cid, "second")

    assert second.status_code == 429
    assert second.json()["error"]["details"]["scope"] == "user"


def test_a_run_that_crosses_80_percent_of_the_global_cap_records_the_alert(
    settings: Settings, db_url: str, make_user: Any, db: psycopg.Connection[Any]
) -> None:
    for client in make_client(settings, db_url, global_daily_budget_usd=Decimal("0.0001")):
        headers = auth(client, make_user)
        cid = new_conversation(client, headers)
        send(client, headers, cid, "hello")

    row = db.execute("SELECT key FROM rate_limits WHERE key LIKE 'spend_alert:%'").fetchall()
    assert [r[0] for r in row] == [f"spend_alert:{TODAY.isoformat()}"]


# ---------------------------------------------------------------- /me/usage


def test_usage_reports_todays_spend_tokens_and_messages(
    chat: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, cid = chat
    before = client.get(f"{API}/me/usage", headers=headers)
    assert before.status_code == 200
    assert before.json()["messages"] == 0
    assert before.json()["spend_usd"] == 0

    send(client, headers, cid, "hello")
    body = client.get(f"{API}/me/usage", headers=headers).json()

    assert body["day"] == TODAY.isoformat()
    assert body["budget_usd"] == 0.5
    assert body["messages"] == 1
    assert body["tokens_used"] > 0
    assert body["spend_usd"] > 0
    assert body["global_cap_reached"] is False
    assert body["resets_at"].endswith("T00:00:00Z")


def test_regenerating_does_not_count_as_another_message(
    chat: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "hello")
    client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)

    assert client.get(f"{API}/me/usage", headers=headers).json()["messages"] == 1


def test_usage_never_shows_another_users_numbers(
    chat: tuple[TestClient, dict[str, str], str], make_user: Any
) -> None:
    client, headers, cid = chat
    send(client, headers, cid, "hello")
    other = auth(client, make_user, email="other@example.com")

    body = client.get(f"{API}/me/usage", headers=other).json()

    assert (body["messages"], body["tokens_used"], body["spend_usd"]) == (0, 0, 0)


def test_usage_needs_a_login(tight: TestClient) -> None:
    assert tight.get(f"{API}/me/usage").status_code == 401


def test_usage_says_when_the_global_cap_is_reached(
    chat: tuple[TestClient, dict[str, str], str],
    db: psycopg.Connection[Any],
    make_user: Any,
) -> None:
    client, headers, _ = chat
    spend(db, make_user("whale@example.com"), "5")

    assert client.get(f"{API}/me/usage", headers=headers).json()["global_cap_reached"] is True
