"""Intent routing and memory by chat over HTTP (TASK-038, TASK-039): real Postgres, checkpointer,
real memory store, the fake models. Slash commands and the router's route reach the client as
`route_selected`; a forget is a card first and a deletion only after the user's decision."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .test_auth import API
from .test_chat import frames, new_conversation, send
from .test_conversations import auth

pytestmark = pytest.mark.integration

Chat = tuple[TestClient, dict[str, str], str]


@pytest.fixture
def chat(client: TestClient, make_user: Any) -> Chat:
    headers = auth(client, make_user)
    return client, headers, new_conversation(client, headers)


def remember(chat: Chat, text: str) -> None:
    client, headers, _ = chat
    assert (
        client.post(f"{API}/memories", headers=headers, json={"content": text}).status_code == 201
    )


def memory_texts(chat: Chat) -> list[str]:
    client, headers, _ = chat
    items = client.get(f"{API}/memories", headers=headers, params={"limit": 100}).json()["items"]
    return [m["content"] for m in items]


def answer(events: list[dict[str, Any]]) -> str:
    return "".join(e["data"]["delta"] for e in events if e["event"] == "token")


def messages(chat: Chat) -> list[dict[str, Any]]:
    client, headers, cid = chat
    page = client.get(f"{API}/conversations/{cid}/messages", headers=headers).json()["items"]
    return list(reversed(page))  # the API lists newest first


def resume(chat: Chat, **body: Any) -> Any:
    client, headers, cid = chat
    return client.post(f"{API}/conversations/{cid}/runs/resume", headers=headers, json=body)


def test_a_new_message_gets_a_route_selected_event_right_after_run_started(chat: Chat) -> None:
    events = frames(send(*chat[:2], chat[2], "hello").text)

    assert [e["event"] for e in events[:2]] == ["run_started", "route_selected"]
    assert events[1]["data"] == {
        "type": "route_selected",
        "intent": "general_chat",
        "source": "router",
        "confidence": 0.95,
    }
    assert messages(chat)[-1]["intent"] == "general_chat"


def test_help_streams_a_fixed_text_with_no_route(chat: Chat) -> None:
    events = frames(send(*chat[:2], chat[2], "/help").text)

    assert [e["event"] for e in events] == ["run_started", "token", "usage", "done"]
    assert "/memory forget" in answer(events)
    assert messages(chat)[-1]["intent"] is None


def test_a_usage_reply_is_streamed_like_any_answer(chat: Chat) -> None:
    events = frames(send(*chat[:2], chat[2], "/memory remember").text)

    assert answer(events).startswith("Usage:") and events[-1]["event"] == "done"


def test_the_memory_list_command_reports_its_route_and_lists_memories(chat: Chat) -> None:
    remember(chat, "Prefers Go for backend work.")

    events = frames(send(*chat[:2], chat[2], "/memory").text)

    assert events[1]["data"] == {
        "type": "route_selected",
        "intent": "manage_memory",
        "source": "command",
        "confidence": None,
    }
    assert "Prefers Go for backend work." in answer(events)
    assert messages(chat)[-1]["intent"] == "manage_memory"


def test_remember_by_command_saves_once_and_a_repeat_adds_nothing(chat: Chat) -> None:
    first = frames(send(*chat[:2], chat[2], "/memory remember I prefer Go for backend work").text)
    again = frames(send(*chat[:2], chat[2], "/memory remember I prefer Go for backend work").text)

    assert [e["data"]["action"] for e in first if e["event"] == "memory_updated"] == ["created"]
    assert [e for e in again if e["event"] == "memory_updated"] == []
    assert "already have that" in answer(again)
    assert memory_texts(chat) == ["User prefers Go for backend work."]


def test_a_sensitive_memory_is_refused_by_chat(chat: Chat) -> None:
    events = frames(send(*chat[:2], chat[2], "/memory remember I have asthma").text)

    assert "sensitive personal information" in answer(events)
    assert memory_texts(chat) == []


def test_a_memory_answer_cannot_be_regenerated(chat: Chat) -> None:
    send(*chat[:2], chat[2], "/memory")
    client, headers, cid = chat

    r = client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)

    assert r.status_code == 409 and r.json()["error"]["code"] == "REGENERATE_NOT_SUPPORTED"


# -- documents (TASK-041) ------------------------------------------------------------------------


def test_pdf_creates_a_document_that_the_message_list_reports(chat: Chat) -> None:
    client, headers, cid = chat
    frames(send(client, headers, cid, "What did we decide about the launch?").text)

    events = frames(send(client, headers, cid, "/pdf").text)

    assert [e["event"] for e in events[:3]] == ["run_started", "route_selected", "document_created"]
    assert events[1]["data"]["intent"] == "generate_document"
    document = events[2]["data"]["document"]
    assert document["status"] == "pending" and document["kind"] == "thread_summary"
    assert document["conversation_id"] == cid
    assert document["message_id"] == events[0]["data"]["assistant_message_id"]
    # The stored answer carries the document, newest message first in the listing.
    newest = client.get(f"{API}/conversations/{cid}/messages", headers=headers).json()["items"][0]
    assert newest["intent"] == "generate_document" and newest["route_source"] == "command"
    assert [d["id"] for d in newest["documents"]] == [document["id"]]
    # And it cannot be regenerated.
    refused = client.post(f"{API}/conversations/{cid}/regenerate", headers=headers)
    assert (
        refused.status_code == 409 and refused.json()["error"]["code"] == "REGENERATE_NOT_SUPPORTED"
    )


def test_messages_without_a_document_list_an_empty_array(chat: Chat) -> None:
    client, headers, cid = chat
    frames(send(client, headers, cid, "hello").text)

    items = client.get(f"{API}/conversations/{cid}/messages", headers=headers).json()["items"]

    assert [m["documents"] for m in items] == [[], []]
    assert items[0]["route_source"] == "router" and items[1]["route_source"] is None


def test_another_users_document_never_shows_in_my_message_list(
    chat: Chat, client: TestClient, make_user: Any
) -> None:
    mine_client, mine_headers, mine_cid = chat
    frames(send(mine_client, mine_headers, mine_cid, "hello").text)
    frames(send(mine_client, mine_headers, mine_cid, "/pdf").text)
    other_headers = auth(client, make_user, "other@example.com")
    other_cid = new_conversation(client, other_headers)
    frames(send(client, other_headers, other_cid, "hello").text)

    theirs = client.get(f"{API}/conversations/{other_cid}/messages", headers=other_headers)

    assert [m["documents"] for m in theirs.json()["items"]] == [[], []]
    assert (
        client.get(f"{API}/conversations/{mine_cid}/messages", headers=other_headers).status_code
        == 404
    )


# -- forget --------------------------------------------------------------------------------------


def start_forget(chat: Chat) -> dict[str, Any]:
    remember(chat, "User works at Acme.")
    remember(chat, "User prefers dark mode.")
    events = frames(send(*chat[:2], chat[2], "/memory forget works at Acme").text)
    assert events[-1]["event"] == "interrupt"
    card: dict[str, Any] = events[-1]["data"]["interrupt"]
    return card


def test_a_forget_ends_the_stream_with_a_card_and_deletes_nothing(chat: Chat) -> None:
    card = start_forget(chat)

    assert card["tool"] == "forget_memory"
    assert [c["content"] for c in card["args"]["candidates"]] == ["User works at Acme."]
    assert set(card) == {"interrupt_id", "tool", "args", "created_at", "expires_at"}
    assert sorted(memory_texts(chat)) == ["User prefers dark mode.", "User works at Acme."]


def test_approving_the_card_deletes_that_memory_and_only_that_one(chat: Chat) -> None:
    card = start_forget(chat)
    chosen = card["args"]["candidates"][0]["memory_id"]

    r = resume(
        chat,
        interrupt_id=card["interrupt_id"],
        decision="approve",
        edited_args={"memory_id": chosen},
    )

    events = frames(r.text)
    assert [e["event"] for e in events if e["event"] == "memory_updated"] == ["memory_updated"]
    assert "Forgot: User works at Acme." in answer(events)
    assert memory_texts(chat) == ["User prefers dark mode."]
    assert messages(chat)[-1]["intent"] == "manage_memory"


def test_rejecting_the_card_keeps_every_memory(chat: Chat) -> None:
    card = start_forget(chat)

    events = frames(resume(chat, interrupt_id=card["interrupt_id"], decision="reject").text)

    assert "didn't delete anything" in answer(events)
    assert len(memory_texts(chat)) == 2


def test_a_memory_the_card_did_not_offer_is_a_422_and_the_card_stays(chat: Chat) -> None:
    card = start_forget(chat)
    client, headers, cid = chat
    other = client.get(f"{API}/memories", headers=headers).json()["items"]
    not_offered = next(
        m["id"] for m in other if m["id"] != card["args"]["candidates"][0]["memory_id"]
    )

    r = resume(
        chat,
        interrupt_id=card["interrupt_id"],
        decision="approve",
        edited_args={"memory_id": not_offered},
    )

    assert r.status_code == 422
    assert (
        client.get(f"{API}/conversations/{cid}", headers=headers).json()["pending_interrupt"]
        == card
    )
    assert len(memory_texts(chat)) == 2


def test_another_users_memory_is_never_offered_or_deleted(
    client: TestClient, make_user: Any, chat: Chat
) -> None:
    remember(chat, "User works at Acme.")
    bob = auth(client, make_user, email="bob@example.com")
    bob_chat: Chat = (client, bob, new_conversation(client, bob))

    events = frames(send(*bob_chat[:2], bob_chat[2], "/memory forget works at Acme").text)

    assert events[-1]["event"] == "done"  # no card: Bob has no such memory
    assert "couldn't find" in answer(events)
    assert memory_texts(chat) == ["User works at Acme."]
