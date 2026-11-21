"""Helpers for run-service tests: scripted models and an event collector."""

import asyncio
from collections.abc import Sequence
from typing import Any

from app.api.sse import Event
from app.llm.fake import FakeChatModel, _Plan
from app.services.runs import Run
from langchain_core.messages import BaseMessage
from pydantic import Field


class NumberedFake(FakeChatModel):
    """Answers "answer 1", "answer 2", ... so a regenerated answer differs from the old one."""

    count: int = Field(default=0)

    def _plan(self, messages: Sequence[BaseMessage]) -> _Plan:
        if self.role != "chat":
            return super()._plan(messages)
        self.count += 1
        return self._plain(f"answer {self.count}", 10)


class ExplodingFake(FakeChatModel):
    """A chat model whose call fails with `error` after the first word."""

    error: Any = None

    async def _astream(self, *args: Any, **kwargs: Any) -> Any:
        if self.role != "chat":
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
            return
        raise self.error


async def collect(run: Run, timeout_s: float = 10.0) -> list[Event]:
    """Everything the SSE layer would send for `run`, until the stream ends."""
    events: list[Event] = []
    async with asyncio.timeout(timeout_s):
        while (event := await run.events.get()) is not None:
            events.append(event)
    return events


def types(events: Sequence[Event]) -> list[str]:
    return [e.type for e in events]
