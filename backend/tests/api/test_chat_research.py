"""Deep research over HTTP (TASK-043): `/research` and the router reach the pipeline, the plan is
a card first and a search only after the user's decision, the report streams into the chat.

Real Postgres, the real checkpointer, the fake models and the offline fake web searcher
(`LLM_MODE=fake`), so the flow is the production one without a network."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .test_auth import API
from .test_chat import frames, new_conversation, send
from .test_conversations import auth, user_id

pytestmark = pytest.mark.integration

TOPIC = "solid-state batteries"
# What the fake router reads: the intent and its slots (the real router reads the words).
PDF_ROUTE = f"[[route:deep_research]][[slot:topic={TOPIC}]][[slot:output=pdf]]"

Chat = tuple[TestClient, dict[str, str], str]


@pytest.fixture
def chat(client: TestClient, make_user: Any) -> Chat:
    headers = auth(client, make_user)
    return client, headers, new_conversation(client, headers)


def resume(chat: Chat, **body: Any) -> Any:
    client, headers, cid = chat
    return client.post(f"{API}/conversations/{cid}/runs/resume", headers=headers, json=body)


def plan(chat: Chat, text: str = f"/research {TOPIC}") -> dict[str, Any]:
    """Ask for research; returns the `research_plan` interrupt."""
    events = frames(send(*chat[:2], chat[2], text).text)
    assert events[-1]["event"] == "interrupt", [e["event"] for e in events]
    payload: dict[str, Any] = events[-1]["data"]["interrupt"]
    return payload


def messages(chat: Chat) -> list[dict[str, Any]]:
    client, headers, cid = chat
    items: list[dict[str, Any]] = client.get(
        f"{API}/conversations/{cid}/messages", headers=headers
    ).json()["items"]
    return list(reversed(items))  # oldest first


def conversation(chat: Chat) -> dict[str, Any]:
    client, headers, cid = chat
    body: dict[str, Any] = client.get(f"{API}/conversations/{cid}", headers=headers).json()
    return body


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in events]


def test_research_pauses_with_the_plan_before_any_search(chat: Chat) -> None:
    client, headers, cid = chat

    events = frames(send(client, headers, cid, f"/research {TOPIC}").text)

    assert kinds(events)[:3] == ["run_started", "route_selected", "progress"]
    assert events[1]["data"] == {
        "type": "route_selected",
        "intent": "deep_research",
        "source": "command",
        "confidence": None,
    }
    assert events[2]["data"] == {
        "type": "progress",
        "stage": "planning",
        "done": None,
        "total": None,
    }
    assert kinds(events)[-1] == "interrupt"
    assert {"tool_start", "tool_end", "token", "usage", "done"}.isdisjoint(kinds(events))
    interrupt = events[-1]["data"]["interrupt"]
    assert interrupt["tool"] == "research_plan"
    assert interrupt["args"]["topic"] == TOPIC
    assert interrupt["args"]["queries"][0] == TOPIC and len(interrupt["args"]["queries"]) == 4
    assert set(interrupt) == {"interrupt_id", "tool", "args", "created_at", "expires_at"}
    assert [m["role"] for m in messages(chat)] == ["user"]  # no answer yet


def test_the_plan_survives_a_reload_and_is_on_the_conversation(chat: Chat) -> None:
    interrupt = plan(chat)

    assert conversation(chat)["pending_interrupt"] == interrupt
    client, headers, _ = chat
    listed = client.get(f"{API}/conversations", headers=headers).json()["items"][0]
    assert listed["pending_interrupt"] == interrupt


def test_approving_searches_the_plan_and_streams_a_cited_report(chat: Chat) -> None:
    interrupt = plan(chat)

    r = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")

    assert r.status_code == 200
    events = frames(r.text)
    assert kinds(events)[0] == "run_started" and events[0]["data"]["user_message_id"] is None
    assert kinds(events)[-2:] == ["usage", "done"]
    searches = [e["data"]["args"]["query"] for e in events if e["event"] == "tool_start"]
    assert sorted(searches) == sorted(interrupt["args"]["queries"])  # exactly what was shown
    progress = [(e["data"]["stage"], e["data"]["done"]) for e in events if e["event"] == "progress"]
    assert progress[0] == ("searching", 0) and progress[-1][0] == "writing"
    assert ("searching", 4) in progress
    streamed = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert streamed.startswith("## Summary") and "[1]" in streamed
    assert not any("queries" in e["data"] for e in events if e["event"] == "token")  # not the plan
    assert conversation(chat)["pending_interrupt"] is None

    report = messages(chat)[1]
    assert report["role"] == "assistant" and report["status"] == "complete"
    assert report["intent"] == "deep_research" and report["route_source"] == "command"
    assert report["content"] == streamed
    assert [c["name"] for c in report["tool_calls"]] == ["web_search"] * 4
    assert report["citations"] and all(c["source_type"] == "web" for c in report["citations"])
    assert report["documents"] == []


def test_an_edit_searches_only_the_edited_queries(chat: Chat) -> None:
    interrupt = plan(chat)

    r = resume(
        chat,
        interrupt_id=interrupt["interrupt_id"],
        decision="edit",
        edited_args={"queries": ["  my   own query ", "a second one"]},
    )

    assert r.status_code == 200
    searches = [e["data"]["args"]["query"] for e in frames(r.text) if e["event"] == "tool_start"]
    assert sorted(searches) == ["a second one", "my own query"]


@pytest.mark.parametrize(
    "edited",
    [
        None,
        {},
        {"queries": []},
        {"queries": ["   "]},
        {"queries": ["ok"] * 7},
        {"queries": ["x" * 401]},
        {"queries": "not a list"},
        {"query": "a web search edit does not edit a plan"},
    ],
)
def test_a_bad_edit_is_a_422_and_the_plan_stays_pending(chat: Chat, edited: Any) -> None:
    interrupt = plan(chat)
    body: dict[str, Any] = {"interrupt_id": interrupt["interrupt_id"], "decision": "edit"}
    if edited is not None:
        body["edited_args"] = edited

    r = resume(chat, **body)

    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert conversation(chat)["pending_interrupt"] == interrupt  # still waiting, nothing searched
    ok = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="reject")
    assert ok.status_code == 200


def test_rejecting_searches_nothing(chat: Chat) -> None:
    interrupt = plan(chat)

    events = frames(resume(chat, interrupt_id=interrupt["interrupt_id"], decision="reject").text)

    assert {"tool_start", "tool_end", "citation"}.isdisjoint(kinds(events))
    answer = messages(chat)[1]
    assert answer["status"] == "complete" and answer["intent"] == "deep_research"
    assert "won't search the web" in answer["content"]
    assert answer["tool_calls"] == [] and answer["citations"] == []


def test_the_approval_is_answered_once(chat: Chat) -> None:
    interrupt = plan(chat)
    assert (
        resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve").status_code == 200
    )

    again = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")

    assert again.status_code == 409 and again.json()["error"]["code"] == "NO_PENDING_INTERRUPT"


def test_a_new_message_abandons_the_plan(chat: Chat) -> None:
    interrupt = plan(chat)
    client, headers, cid = chat

    frames(send(client, headers, cid, "never mind, hello").text)

    assert conversation(chat)["pending_interrupt"] is None
    late = resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")
    assert late.status_code == 409


def test_a_research_answer_cannot_be_regenerated(chat: Chat) -> None:
    interrupt = plan(chat)
    resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")
    client, headers, cid = chat

    refused = client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)

    assert (
        refused.status_code == 409 and refused.json()["error"]["code"] == "REGENERATE_NOT_SUPPORTED"
    )


def test_the_router_reaches_research_and_a_pdf_is_made_from_the_report(
    chat: Chat, db: psycopg.Connection[Any]
) -> None:
    text = f"Research {TOPIC} as a PDF {PDF_ROUTE}"
    events = frames(send(*chat[:2], chat[2], text).text)
    assert (
        events[1]["data"]["intent"] == "deep_research" and events[1]["data"]["source"] == "router"
    )
    interrupt = events[-1]["data"]["interrupt"]

    events = frames(resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve").text)

    created = [e["data"]["document"] for e in events if e["event"] == "document_created"]
    assert [d["kind"] for d in created] == ["research_report"] and created[0]["status"] == "pending"
    assert created[0]["message_id"] == events[0]["data"]["assistant_message_id"]
    assert [e["data"]["stage"] for e in events if e["event"] == "progress"][-2:] == [
        "writing",
        "rendering",
    ]
    report = messages(chat)[1]
    assert [d["id"] for d in report["documents"]] == [created[0]["id"]]
    assert report["content"].endswith("It's being prepared now.")
    row = db.execute("SELECT kind, status, title FROM documents").fetchone()
    assert row == ("research_report", "pending", TOPIC)
    assert db.execute("SELECT count(*) FROM jobs WHERE kind = 'render_document'").fetchone() == (1,)


def test_a_command_is_not_something_to_remember(chat: Chat, db: psycopg.Connection[Any]) -> None:
    interrupt = plan(chat)
    resume(chat, interrupt_id=interrupt["interrupt_id"], decision="approve")

    assert db.execute("SELECT count(*) FROM jobs WHERE kind = 'extract_memories'").fetchone() == (
        0,
    )


def test_a_run_that_could_cost_more_than_the_budget_left_does_not_start(
    chat: Chat, db: psycopg.Connection[Any]
) -> None:
    db.execute(
        "INSERT INTO usage_daily (user_id, day, cost_usd) VALUES (%s, %s, %s)",
        (user_id(db), datetime.now(UTC).date(), Decimal("0.49")),
    )

    events = frames(send(*chat[:2], chat[2], f"/research {TOPIC}").text)

    text = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert "can't start a research run" in text and "$0.01" in text
    assert kinds(events)[-2:] == ["usage", "done"] and "interrupt" not in kinds(events)
    assert conversation(chat)["pending_interrupt"] is None


def test_the_daily_document_limit_is_told_up_front_when_a_pdf_is_asked_for(
    chat: Chat, db: psycopg.Connection[Any]
) -> None:
    owner = user_id(db)
    for _ in range(10):
        db.execute(
            "INSERT INTO documents (user_id, title, kind, status, draft)"
            " VALUES (%s, 't', 'answer', 'ready', '{}'::jsonb)",
            (owner,),
        )
    text = f"Research {TOPIC} as a PDF {PDF_ROUTE}"

    events = frames(send(*chat[:2], chat[2], text).text)

    said = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert "limit of 10 documents" in said and "interrupt" not in kinds(events)
    # A chat report is not a document: the same user can still research into the chat.
    assert frames(send(*chat[:2], chat[2], f"/research {TOPIC}").text)[-1]["event"] == "interrupt"


def test_another_user_cannot_answer_the_plan(
    chat: Chat, client: TestClient, make_user: Any
) -> None:
    interrupt = plan(chat)
    _, _, cid = chat
    other = auth(client, make_user, email="mallory@example.com")

    r = client.post(
        f"{API}/conversations/{cid}/runs/resume",
        headers=other,
        json={"interrupt_id": interrupt["interrupt_id"], "decision": "approve"},
    )

    assert r.status_code == 404
    assert conversation(chat)["pending_interrupt"] == interrupt


def test_help_lists_research_once_it_is_built(chat: Chat) -> None:
    events = frames(send(*chat[:2], chat[2], "/help").text)
    assert "/research <question>" in "".join(
        e["data"]["delta"] for e in events if e["event"] == "token"
    )


def test_a_research_command_without_a_question_gets_a_usage_reply(chat: Chat) -> None:
    events = frames(send(*chat[:2], chat[2], "/research").text)
    said = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert said.startswith("Usage: `/research <question>`") and "interrupt" not in kinds(events)
