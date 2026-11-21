from app.graph.nodes.summarize import split_for_summary
from app.llm.tokens import count_messages
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from tests.graph.support import filler


def convo(turns: int, tokens: int = 100) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for i in range(turns):
        out.append(HumanMessage(f"u{i} {filler(tokens)}", id=f"h{i}"))
        out.append(AIMessage(f"a{i} {filler(tokens)}", id=f"a{i}"))
    return out


def test_keeps_the_most_recent_messages_within_the_budget_starting_on_a_human_message() -> None:
    messages = convo(10)
    cut = split_for_summary(messages, keep_tokens=500)
    kept = messages[cut:]
    assert isinstance(kept[0], HumanMessage)
    assert count_messages(kept) <= 500 + count_messages(messages[-2:])
    assert cut > 0
    assert messages[-2:] == kept[-2:]


def test_always_keeps_the_newest_user_message_even_when_it_exceeds_the_budget() -> None:
    messages = [*convo(3), HumanMessage(filler(5000), id="big")]
    cut = split_for_summary(messages, keep_tokens=100)
    assert messages[cut:][0].id == "big"
    assert cut == len(messages) - 1


def test_never_leaves_an_orphan_tool_message_at_the_start_of_the_kept_part() -> None:
    messages = [
        HumanMessage(f"q {filler(200)}", id="h0"),
        AIMessage("", id="a0", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
        ToolMessage(filler(200), tool_call_id="c1", id="t0"),
        AIMessage(f"done {filler(200)}", id="a1"),
        HumanMessage("next", id="h1"),
    ]
    # A budget that would land the boundary on the tool message / the AI that called it.
    for budget in range(50, 800, 25):
        cut = split_for_summary(messages, keep_tokens=budget)
        kept = messages[cut:]
        assert isinstance(kept[0], HumanMessage), budget
        assert kept[-1].id == "h1"


def test_nothing_to_remove_returns_zero() -> None:
    messages = [HumanMessage(filler(5000), id="only")]
    assert split_for_summary(messages, keep_tokens=100) == 0
