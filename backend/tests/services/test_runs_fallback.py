"""A run whose primary model is down (TASK-031): the fallback answers, and when both are down the
run ends as a safe error and the conversation is free again. Uses the real model factory in fake
mode, so the same primary + fallback chain as production sits under the graph."""

from typing import Any

import pytest
from app.api.sse import DoneEvent, ErrorEvent, TokenEvent
from app.core.config import Settings
from app.llm.factory import get_model

from tests.services.support import Env, collect, types

pytestmark = pytest.mark.integration


async def test_a_run_whose_primary_model_is_down_is_answered_by_the_fallback(
    make_env: Any, settings: Settings
) -> None:
    env: Env = await make_env(chat=get_model("chat", settings=settings))

    events = await collect(await env.send("hello [[fail:primary]]"))

    assert types(events)[-1] == "done"
    assert isinstance(events[-1], DoneEvent) and events[-1].finish_reason == "stop"
    streamed = "".join(e.delta for e in events if isinstance(e, TokenEvent))
    assert streamed == "Fake reply: hello [[fail:primary]]"
    assert env.messages()[1] == (
        "assistant",
        "Fake reply: hello [[fail:primary]]",
        "complete",
        "stop",
    )
    assert env.lease() == (None, None)


async def test_when_both_models_are_down_the_run_fails_safely_and_the_chat_is_free(
    make_env: Any, settings: Settings
) -> None:
    env: Env = await make_env(chat=get_model("chat", settings=settings))

    events = await collect(await env.send("hello [[fail:all]]"))

    assert types(events) == ["run_started", "error"]
    err = events[-1]
    assert isinstance(err, ErrorEvent)
    assert (err.code, err.retryable) == ("LLM_UNAVAILABLE", True)
    assert env.messages()[1] == ("assistant", "", "failed", "error")
    assert env.lease() == (None, None)

    # The user can simply try again.
    retry = await collect(await env.send("hello again"))
    assert types(retry)[-1] == "done"
