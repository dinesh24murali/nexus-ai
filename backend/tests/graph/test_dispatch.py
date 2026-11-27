"""Routing inside the real graph: the router runs beside `load_context`, `dispatch` picks the
pipeline, and a resume carries on in the pipeline that paused (never a second routing)."""

import uuid
from typing import Any

import pytest
from app.graph.builder import build_graph
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tests.graph.memory_support import StubManager, configurable
from tests.graph.support import RecordingFake

USER = uuid.uuid4()


class Routed:
    """A graph with a recording chat model, a recording router and a stub memory manager."""

    def __init__(self, *, manager: StubManager | None = None, with_router: bool = True) -> None:
        self.chat = RecordingFake(role="chat")
        self.router = RecordingFake(role="router")
        self.manager = manager if manager is not None else StubManager()
        self.graph = build_graph(
            checkpointer=InMemorySaver(),
            chat_model=self.chat,
            summarize_model=RecordingFake(role="summarize"),
            router_model=self.router if with_router else None,
            memory_manager=self.manager,
        )
        self.config: Any = {"configurable": configurable(USER)}

    async def run(
        self, text: str | None, *, resume: Any = None, **extra: Any
    ) -> list[tuple[str, Any]]:
        """The run's stream, as (mode, payload) pairs of the custom events and the final state."""
        config: Any = {"configurable": {**self.config["configurable"], **extra}}
        graph_input: Any = (
            Command(resume=resume) if text is None else {"messages": [HumanMessage(text)]}
        )
        events: list[tuple[str, Any]] = []
        async for mode, payload in self.graph.astream(graph_input, config, stream_mode=["custom"]):
            events.append((mode, payload))
        return events

    async def state(self) -> Any:
        return await self.graph.aget_state(self.config)

    async def answer(self) -> str:
        state = await self.state()
        last = state.values["messages"][-1]
        assert isinstance(last, AIMessage)
        return last.text


def routes(events: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [p for _, p in events if isinstance(p, dict) and p.get("kind") == "route"]


async def test_an_ordinary_message_is_a_chat_message_and_the_route_is_reported() -> None:
    t = Routed()

    events = await t.run("hello there")

    assert [(r["intent"], r["source"]) for r in routes(events)] == [("general_chat", "router")]
    assert await t.answer() == "Fake reply: hello there"
    assert len(t.router.requests) == 1 and len(t.chat.requests) == 1


async def test_a_memory_request_goes_to_manage_memory_and_the_chat_model_is_never_called() -> None:
    t = Routed()
    t.manager.add(USER, "User prefers Go.")

    events = await t.run("[[route:manage_memory]][[slot:memory_action=list]] what do you know?")

    assert [r["intent"] for r in routes(events)] == ["manage_memory"]
    assert "User prefers Go." in await t.answer()
    assert t.chat.requests == []


async def test_a_graph_without_a_router_is_the_plain_chat_graph() -> None:
    t = Routed(with_router=False)

    events = await t.run("[[route:manage_memory]][[slot:memory_action=list]] hi")

    assert routes(events) == []
    assert t.router.requests == [] and len(t.chat.requests) == 1


@pytest.mark.parametrize(
    "marker",
    [
        "[[route:invalid]]",  # not JSON
        "[[route:manage_memory:0.2]][[slot:memory_action=list]]",  # not sure enough
        "[[route:generate_document]][[slot:source=thread]]",  # not built yet
        "[[route:teleport]]",  # unknown intent
    ],
)
async def test_a_router_that_fails_or_doubts_hands_over_to_chat(marker: str) -> None:
    t = Routed()

    events = await t.run(f"{marker} hello")

    assert [(r["intent"], r["source"]) for r in routes(events)] == [("general_chat", "fallback")]
    assert len(t.chat.requests) == 1


async def test_a_slash_command_skips_the_router_model() -> None:
    t = Routed()
    t.manager.add(USER, "User likes tea.")

    events = await t.run(
        "/memory",
        command={"name": "memory", "intent": "manage_memory", "slots": {"memory_action": "list"}},
    )

    assert [(r["intent"], r["source"], r["confidence"]) for r in routes(events)] == [
        ("manage_memory", "command", None)
    ]
    assert t.router.requests == []
    assert "User likes tea." in await t.answer()


async def test_the_kill_switch_sends_everything_to_chat() -> None:
    t = Routed()

    events = await t.run(
        "[[route:manage_memory]][[slot:memory_action=list]] hi", router_disabled=True
    )

    assert [(r["intent"], r["source"]) for r in routes(events)] == [("general_chat", "fallback")]
    assert t.router.requests == []


async def test_the_router_sees_the_previous_turns_intent() -> None:
    t = Routed()
    await t.run("[[route:manage_memory]][[slot:memory_action=list]] list")

    await t.run("and now?")

    assert "<previous_intent>manage_memory</previous_intent>" in t.router.requests[-1][-1].text
    assert "<previous_intent>none</previous_intent>" in t.router.requests[0][-1].text


# -- forget: a card before anything is deleted, and a resume that does not route again ---------


def forget_text(query: str = "works at acme") -> str:
    return (
        "[[route:manage_memory]][[slot:memory_action=forget]]"
        f"[[slot:memory_content={query}]] forget"
    )


async def start_forget(t: StubManager | None = None) -> tuple[Routed, list[Any]]:
    manager = t or StubManager()
    manager.add(USER, "User works at Acme.", "User prefers dark mode.")
    manager.scores = {"User works at Acme.": 0.9, "User prefers dark mode.": 0.2}
    routed = Routed(manager=manager)
    await routed.run(forget_text())
    state = await routed.state()
    return routed, [i.value for i in state.interrupts]


async def test_a_forget_asks_before_it_deletes_anything() -> None:
    t, interrupts = await start_forget()

    assert len(interrupts) == 1
    assert interrupts[0]["tool"] == "forget_memory"
    assert [c["content"] for c in interrupts[0]["args"]["candidates"]] == ["User works at Acme."]
    assert t.manager.deleted == []  # nothing happened yet
    assert t.chat.requests == []


async def test_approving_deletes_exactly_the_one_memory_and_says_so() -> None:
    t, interrupts = await start_forget()
    chosen = interrupts[0]["args"]["candidates"][0]["memory_id"]

    events = await t.run(None, resume={"decision": "approve", "args": {"memory_id": chosen}})

    assert t.manager.texts(USER) == ["User prefers dark mode."]
    assert "Forgot: User works at Acme." in await t.answer()
    assert {"kind": "memory_updated", "action": "deleted", "content": "User works at Acme."} in [
        p for _, p in events
    ]
    assert routes(events) == []  # a resume never routes again
    assert len(t.router.requests) == 1  # the original message only


async def test_with_one_candidate_the_id_may_be_left_out() -> None:
    t, _ = await start_forget()

    await t.run(None, resume={"decision": "approve"})

    assert t.manager.texts(USER) == ["User prefers dark mode."]


async def test_rejecting_deletes_nothing() -> None:
    t, _ = await start_forget()

    await t.run(None, resume={"decision": "reject"})

    assert t.manager.deleted == [] and len(t.manager.texts(USER)) == 2
    assert "didn't delete anything" in await t.answer()


@pytest.mark.parametrize(
    "answer",
    [
        "approve",
        ["approve"],
        {"decision": "maybe"},
        {"decision": "edit", "args": {"query": "x"}},
        {"decision": "approve", "args": {"memory_id": str(uuid.uuid4())}},  # not offered
        {"decision": "approve", "args": {"memory_id": ["a"]}},
    ],
)
async def test_anything_but_a_clear_approval_of_an_offered_memory_deletes_nothing(
    answer: Any,
) -> None:
    t, _ = await start_forget()

    await t.run(None, resume=answer)

    assert t.manager.deleted == []


async def test_several_close_matches_are_all_offered_and_one_must_be_named() -> None:
    manager = StubManager()
    manager.add(USER, "User works at Acme.", "User works remotely.")
    manager.scores = {"User works at Acme.": 0.8, "User works remotely.": 0.78}
    t = Routed(manager=manager)
    await t.run(forget_text("my job"))
    candidates = next(i.value for i in (await t.state()).interrupts)["args"]["candidates"]
    assert [c["content"] for c in candidates] == ["User works at Acme.", "User works remotely."]

    await t.run(None, resume={"decision": "approve"})  # which one?
    assert manager.deleted == []


async def test_nothing_close_enough_means_nothing_to_confirm() -> None:
    manager = StubManager()
    manager.add(USER, "User prefers dark mode.")
    manager.scores = {"User prefers dark mode.": 0.5}
    t = Routed(manager=manager)

    await t.run(forget_text("my pet"))

    assert (await t.state()).interrupts == ()
    assert "couldn't find" in await t.answer()
    assert manager.deleted == []


async def test_a_new_message_abandons_the_card_and_deletes_nothing() -> None:
    t, _ = await start_forget()

    await t.run("never mind, what is 2+2?")

    assert t.manager.deleted == [] and len(t.manager.texts(USER)) == 2
    assert (await t.state()).interrupts == ()


async def test_a_switch_flipped_while_the_card_waited_still_holds() -> None:
    t, _ = await start_forget()

    await t.run(None, resume={"decision": "approve"}, memory_writes_disabled=True)

    assert t.manager.deleted == []
    assert "switched off" in await t.answer()


async def test_memory_turned_off_while_the_card_waited_deletes_nothing() -> None:
    t, _ = await start_forget()
    t.manager.is_enabled = False

    await t.run(None, resume={"decision": "approve"})

    assert t.manager.deleted == []


# -- other actions -----------------------------------------------------------------------------


async def test_saving_stores_and_reports_the_memory() -> None:
    t = Routed()

    events = await t.run(
        "[[route:manage_memory]][[slot:memory_action=save]]"
        "[[slot:memory_content=User's timezone is IST.]] remember"
    )

    assert t.manager.saved == [(USER, "User's timezone is IST.")]
    assert "Saved: User's timezone is IST." in await t.answer()
    assert {
        "kind": "memory_updated",
        "action": "created",
        "content": "User's timezone is IST.",
    } in [p for _, p in events]
    assert {"kind": "spend", "embedding_tokens": 7} in [p for _, p in events]


async def test_a_refused_save_says_why_and_stores_nothing() -> None:
    t = Routed()
    t.manager.refuse = "sensitive:health"

    await t.run(
        "[[route:manage_memory]][[slot:memory_action=save]][[slot:memory_content=I have asthma]] x"
    )

    assert "sensitive personal information" in await t.answer()
    assert t.manager.saved == []


async def test_a_duplicate_save_says_it_is_already_there() -> None:
    t = Routed()
    t.manager.duplicate = True

    await t.run(
        "[[route:manage_memory]][[slot:memory_action=save]]"
        "[[slot:memory_content=User likes tea.]] x"
    )

    assert "already have that" in await t.answer()


async def test_memory_off_gives_a_short_notice_for_every_action() -> None:
    for action in ("list", "save", "forget"):
        t = Routed()
        t.manager.is_enabled = False
        await t.run(
            f"[[route:manage_memory]][[slot:memory_action={action}]][[slot:memory_content=x]] hi"
        )
        assert "turned off" in await t.answer()
        assert [name for name, _ in t.manager.calls] == ["enabled"]


async def test_the_switch_blocks_saving_and_forgetting_but_not_listing() -> None:
    t = Routed()
    t.manager.add(USER, "User likes tea.")
    for action, expected in (
        ("save", "switched off"),
        ("forget", "switched off"),
        ("list", "likes tea"),
    ):
        t = Routed(manager=t.manager)
        await t.run(
            f"[[route:manage_memory]][[slot:memory_action={action}]][[slot:memory_content=x]] hi",
            memory_writes_disabled=True,
        )
        assert expected in await t.answer()
    assert t.manager.saved == [] and t.manager.deleted == []


async def test_every_manager_call_is_for_the_runs_own_user() -> None:
    other = uuid.uuid4()
    manager = StubManager()
    manager.add(other, "User works at Acme.")
    manager.scores = {"User works at Acme.": 0.99}
    t = Routed(manager=manager)

    await t.run(forget_text())

    assert {user for _, user in manager.calls} == {USER}
    assert (await t.state()).interrupts == ()  # the other user's memory was never found
    assert manager.texts(other) == ["User works at Acme."]
