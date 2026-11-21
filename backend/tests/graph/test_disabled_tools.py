"""`TOOLS_DISABLED` (kill switch, doc 10 section 3): a switched-off tool is not offered, not asked
about and never runs, including a search that was already waiting for approval."""

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.graph.builder import build_graph
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from tests.graph.retrieval_support import StubRetriever
from tests.graph.support import RecordingFake, thread
from tests.graph.web_support import StubWeb

FIXTURES = Path(__file__).parent / "fixtures"
USER = "33333333-3333-4333-8333-333333333333"


class BindLog(RecordingFake):
    """Remembers which tools were bound to the model behind every call (None: no tools)."""

    bound: list[str] | None = None
    log: list[list[str] | None] = Field(default_factory=list)  # shared by the copies

    def _plan(self, messages: Sequence[BaseMessage]) -> Any:
        self.log.append(self.bound)
        return super()._plan(messages)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BindLog:
        return self.model_copy(update={"bound": sorted(t.name for t in tools)})


def make(web: StubWeb) -> tuple[Any, BindLog]:
    fake = BindLog(fixtures_dir=FIXTURES)
    graph = build_graph(
        checkpointer=InMemorySaver(),
        chat_model=fake,
        summarize_model=RecordingFake(role="summarize"),
        retriever=StubRetriever(),
        web_searcher=web,
        calculator=True,
    )
    return graph, fake


def config(disabled: Sequence[str] = ()) -> RunnableConfig:
    return thread(f"conv-{uuid.uuid4()}", user_id=USER, disabled_tools=list(disabled))


async def test_every_tool_is_offered_when_nothing_is_switched_off() -> None:
    graph, fake = make(StubWeb())

    await graph.ainvoke({"messages": [HumanMessage("hello")]}, config())

    assert fake.log == [["calculator", "search_my_files", "web_search"]]


async def test_a_switched_off_tool_is_not_offered() -> None:
    graph, fake = make(StubWeb())

    await graph.ainvoke({"messages": [HumanMessage("hello")]}, config(["web_search"]))

    assert fake.log == [["calculator", "search_my_files"]]


async def test_with_every_tool_switched_off_the_model_gets_none() -> None:
    graph, fake = make(StubWeb())

    await graph.ainvoke(
        {"messages": [HumanMessage("hello")]},
        config(["web_search", "calculator", "search_my_files"]),
    )

    assert fake.log == [None]


async def test_a_call_to_a_switched_off_tool_is_refused_without_asking() -> None:
    web = StubWeb()
    graph, _ = make(web)
    cfg = config(["web_search"])

    result = await graph.ainvoke({"messages": [HumanMessage("[[fixture:web]] news")]}, cfg)

    state = await graph.aget_state(cfg)
    assert state.interrupts == () and state.next == ()
    assert web.queries == []
    (message,) = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert message.status == "error"
    assert "unavailable" in message.text


async def test_a_search_waiting_for_approval_does_not_run_after_the_switch_is_flipped() -> None:
    web = StubWeb()
    graph, _ = make(web)
    thread_id = f"conv-{uuid.uuid4()}"
    before = thread(thread_id, user_id=USER, disabled_tools=[])
    after = thread(thread_id, user_id=USER, disabled_tools=["web_search"])
    await graph.ainvoke({"messages": [HumanMessage("[[fixture:web]] news")]}, before)
    assert (await graph.aget_state(before)).next == ("approval",)

    await graph.ainvoke(Command(resume={"decision": "approve"}), after)

    assert web.queries == []
    state = await graph.aget_state(after)
    assert state.next == () and state.interrupts == ()
    (message,) = [m for m in state.values["messages"] if isinstance(m, ToolMessage)]
    assert "unavailable" in message.text
    assert state.values["messages"][-1].type == "ai"
