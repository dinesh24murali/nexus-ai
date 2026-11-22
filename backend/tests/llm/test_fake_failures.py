"""Scripted model failures (TASK-031): `[[fail:primary]]` / `[[fail:all]]` in the newest message.

Fake mode builds the same primary + fallback chain as real mode, so the fallback wiring (which
errors it catches, a stream that starts over on the second model) is exercised without a network.
"""

from typing import Any

import openai
import pytest
from app.core.config import Settings
from app.graph.tools.calculator import tool_spec as calculator_spec
from app.llm.factory import get_model
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableWithFallbacks


def chat(settings: Settings, **kwargs: Any) -> Any:
    return get_model("chat", settings=settings, **kwargs)


async def stream_text(model: Any, text: str) -> tuple[str, list[AIMessageChunk]]:
    chunks = [chunk async for chunk in model.astream([HumanMessage(text)])]
    return "".join(str(c.content) for c in chunks), chunks


def test_the_chat_roles_are_a_primary_with_a_fallback_like_in_real_mode(settings: Settings) -> None:
    assert isinstance(chat(settings), RunnableWithFallbacks)
    assert isinstance(get_model("long_form", settings=settings), RunnableWithFallbacks)


@pytest.mark.parametrize("role", ["summarize", "title", "memory_extract", "judge", "router"])
def test_roles_without_a_fallback_stay_plain_models(settings: Settings, role: Any) -> None:
    assert not isinstance(get_model(role, settings=settings), RunnableWithFallbacks)


async def test_without_a_marker_the_primary_answers_and_reports_no_model_name(
    settings: Settings,
) -> None:
    reply = await chat(settings).ainvoke([HumanMessage("hello")])

    assert reply.content == "Fake reply: hello"
    assert "model_name" not in reply.response_metadata  # billed to the configured chat model


async def test_a_primary_failure_is_answered_by_the_fallback_model(settings: Settings) -> None:
    reply = await chat(settings).ainvoke([HumanMessage("hello [[fail:primary]]")])

    assert isinstance(reply, AIMessage)
    assert reply.content == "Fake reply: hello [[fail:primary]]"
    assert reply.response_metadata["model_name"] == settings.llm_fallback_model


async def test_a_streamed_answer_starts_over_on_the_fallback(settings: Settings) -> None:
    text, chunks = await stream_text(chat(settings), "one two three [[fail:primary]]")

    assert text == "Fake reply: one two three [[fail:primary]]"
    # LangChain may add an empty closing chunk, so look at what the chunks add up to.
    merged = sum(chunks[1:], chunks[0])
    assert merged.response_metadata["model_name"] == settings.llm_fallback_model


async def test_a_scripted_fixture_still_plays_on_the_fallback(settings: Settings) -> None:
    reply = await chat(settings, tools=[calculator_spec()]).ainvoke(
        [HumanMessage("[[fixture:calc]] [[fail:primary]]")]
    )

    assert reply.tool_calls  # the fixture's tool call came through the fallback, tools bound


async def test_when_both_models_fail_the_error_reaches_the_caller(settings: Settings) -> None:
    with pytest.raises(openai.APIConnectionError):
        await chat(settings).ainvoke([HumanMessage("hello [[fail:all]]")])
    with pytest.raises(openai.APIConnectionError):
        await stream_text(chat(settings), "hello [[fail:all]]")


async def test_the_marker_only_counts_in_the_newest_message(settings: Settings) -> None:
    messages = [HumanMessage("old [[fail:all]]"), AIMessage("answer"), HumanMessage("new")]

    reply = await chat(settings).ainvoke(messages)

    assert reply.content == "Fake reply: new"


async def test_helper_roles_ignore_the_markers(settings: Settings) -> None:
    reply = await get_model("title", settings=settings).ainvoke(
        [HumanMessage("hello [[fail:all]]")]
    )

    assert reply.content == "Fake title"
