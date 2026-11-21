import asyncio
from typing import Any

from app.graph.builder import build_graph
from app.llm.fake import SUMMARY_TEXT, FakeChatModel
from evals.datasets import Turn
from evals.runner import run_chat, run_summary, to_messages
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from tests.graph.support import RecordingFake


def graph_with(model: Any) -> Any:
    return build_graph(checkpointer=None, chat_model=model, summarize_model=FakeChatModel())


async def test_a_chat_case_returns_the_answer_with_timing_and_usage() -> None:
    result = await run_chat(graph_with(FakeChatModel()), default_model="m", text="hello")
    assert result.error is None
    assert result.output == "Fake reply: hello"
    assert result.ttft_ms is not None and 0 <= result.ttft_ms <= result.total_ms
    assert result.usage.prompt_tokens > 0 and result.usage.completion_tokens > 0
    assert result.cost_usd > 0


async def test_history_is_part_of_the_request_but_not_of_the_next_case() -> None:
    model = RecordingFake()
    history = [Turn(role="user", content="earlier q"), Turn(role="assistant", content="earlier a")]
    await run_chat(graph_with(model), default_model="m", text="now", history=history)
    texts = [m.text for m in model.requests[0]]
    assert "earlier q" in texts and "earlier a" in texts and "now" in texts

    await run_chat(graph_with(model), default_model="m", text="other")
    assert not any("earlier" in m.text for m in model.requests[1])


async def test_a_failing_model_is_recorded_not_raised_and_the_message_is_not_kept() -> None:
    def boom(_: Any) -> AIMessage:
        raise RuntimeError("secret text in the error")

    result = await run_chat(graph_with(RunnableLambda(boom)), default_model="m", text="hi")
    assert result.error == "RuntimeError"  # the class only: error text may carry user content
    assert result.output == ""


async def test_a_slow_model_times_out() -> None:
    async def slow(_: Any) -> AIMessage:
        await asyncio.sleep(5)
        return AIMessage("late")

    result = await run_chat(
        graph_with(RunnableLambda(slow)), default_model="m", text="hi", timeout_s=0.05
    )
    assert result.error == "TimeoutError"


async def test_an_empty_answer_counts_as_an_error() -> None:
    result = await run_chat(
        graph_with(RunnableLambda(lambda _: AIMessage(""))), default_model="m", text="hi"
    )
    assert result.error == "EmptyAnswer"


async def test_a_model_that_does_not_stream_still_gets_a_first_token_time() -> None:
    result = await run_chat(
        graph_with(RunnableLambda(lambda _: AIMessage("whole answer"))),
        default_model="m",
        text="hi",
    )
    assert result.output == "whole answer"
    assert result.ttft_ms == result.total_ms


def test_turns_become_messages() -> None:
    messages = to_messages([Turn(role="user", content="a"), Turn(role="assistant", content="b")])
    assert isinstance(messages[0], HumanMessage) and isinstance(messages[1], AIMessage)


async def test_the_whole_case_is_folded_into_the_summary() -> None:
    model = RecordingFake(role="summarize")
    turns = [Turn(role="user", content="fact one"), Turn(role="assistant", content="noted")]
    assert await run_summary(model, turns) == SUMMARY_TEXT
    sent = model.requests[0][0].text
    assert "fact one" in sent and "noted" in sent  # nothing was kept back from the summariser
    assert "(continue)" not in sent  # the closing user message is the part that is kept


async def test_a_failing_summariser_gives_none() -> None:
    def boom(_: Any) -> AIMessage:
        raise RuntimeError("down")

    turns = [Turn(role="user", content="a"), Turn(role="assistant", content="b")]
    assert await run_summary(RunnableLambda(boom), turns) is None
    assert await run_summary(RunnableLambda(lambda _: AIMessage("   ")), turns) is None
