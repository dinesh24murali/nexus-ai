"""Streaming behaviour that needs a real socket: disconnects, cancel mid-stream, pings."""

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import psycopg
import pytest
from app.api import sse

pytestmark = pytest.mark.integration

LONG = "[[fixture:long]]"


@pytest.fixture
def api(live_server: Callable[..., str], make_user: Any) -> Iterator[tuple[str, httpx.Client, str]]:
    base = live_server(llm_fake_token_delay_s=0.02)
    make_user()
    with httpx.Client(timeout=30) as http:
        token = http.post(
            f"{base}/auth/login",
            json={"email": "marco@example.com", "password": "correct-horse-battery"},
        ).json()["access_token"]
        http.headers["authorization"] = f"Bearer {token}"
        cid = http.post(f"{base}/conversations", json={}).json()["id"]
        yield base, http, cid


def events(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    event: dict[str, Any] = {}
    for line in lines:
        if line.startswith("event: "):
            event = {"event": line.removeprefix("event: ")}
        elif line.startswith("data: "):
            event["data"] = json.loads(line.removeprefix("data: "))
            yield event


def wait_for(db: psycopg.Connection[Any], sql: str, expected: Any, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db.execute(sql).fetchone()
        if row == expected:
            return
        time.sleep(0.05)
    pytest.fail(f"{sql!r} never returned {expected!r}; last: {db.execute(sql).fetchone()!r}")


def test_a_client_that_disconnects_mid_stream_does_not_stop_the_run(
    api: tuple[str, httpx.Client, str], db: psycopg.Connection[Any]
) -> None:
    base, http, cid = api
    seen = 0
    with http.stream(
        "POST", f"{base}/conversations/{cid}/messages", json={"content": f"{LONG} hi"}
    ) as r:
        for event in events(r.iter_lines()):
            seen += event["event"] == "token"
            if seen == 3:
                break  # leave: the connection closes with the run still going
    assert seen == 3

    wait_for(
        db,
        "SELECT status, finish_reason FROM messages WHERE role = 'assistant'",
        ("complete", "stop"),
    )
    row = db.execute("SELECT content FROM messages WHERE role = 'assistant'").fetchone()
    assert (
        row and row[0].startswith("This is a long scripted answer") and row[0].endswith("The end.")
    )
    wait_for(db, "SELECT active_run_id FROM conversations", (None,))


def test_cancel_mid_stream_ends_the_stream_with_a_cancelled_done(
    api: tuple[str, httpx.Client, str], db: psycopg.Connection[Any]
) -> None:
    base, http, cid = api
    received: list[dict[str, Any]] = []
    cancel_status = 0
    with http.stream(
        "POST", f"{base}/conversations/{cid}/messages", json={"content": f"{LONG} hi"}
    ) as r:
        for event in events(r.iter_lines()):
            received.append(event)
            tokens = sum(e["event"] == "token" for e in received)
            if tokens == 4 and not cancel_status:
                cancel_status = http.post(f"{base}/conversations/{cid}/runs/cancel").status_code

    assert cancel_status == 202
    assert [e["event"] for e in received][-2:] == ["usage", "done"]
    assert received[-1]["data"]["finish_reason"] == "cancelled"
    streamed = "".join(e["data"]["delta"] for e in received if e["event"] == "token")
    row = db.execute(
        "SELECT content, status, finish_reason FROM messages WHERE role = 'assistant'"
    ).fetchone()
    assert row == (streamed, "cancelled", "cancelled")
    assert 0 < len(streamed) < 200
    conv = http.get(f"{base}/conversations/{cid}").json()
    assert conv["has_active_run"] is False


def test_deleting_a_conversation_stops_its_run_first(
    api: tuple[str, httpx.Client, str], db: psycopg.Connection[Any]
) -> None:
    base, http, cid = api
    deleted = 0
    last = ""
    with http.stream(
        "POST", f"{base}/conversations/{cid}/messages", json={"content": f"{LONG} hi"}
    ) as r:
        for event in events(r.iter_lines()):
            last = event["event"]
            if event["event"] == "token" and not deleted:
                deleted = http.delete(f"{base}/conversations/{cid}").status_code

    assert deleted == 204
    assert last in {"done", "error"}  # the stream ended instead of hanging
    assert db.execute("SELECT count(*) FROM conversations").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM messages").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM checkpoints").fetchone() == (0,)


def test_a_ping_comment_keeps_a_quiet_stream_alive(
    api: tuple[str, httpx.Client, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    base, http, cid = api
    monkeypatch.setattr(sse, "PING_INTERVAL_S", 0.01)  # the model pauses 20 ms between words

    with http.stream(
        "POST", f"{base}/conversations/{cid}/messages", json={"content": f"{LONG} hi"}
    ) as r:
        body = "\n".join(r.iter_lines())

    assert ": ping" in body
    assert body.rstrip().endswith('"finish_reason":"stop"}')
