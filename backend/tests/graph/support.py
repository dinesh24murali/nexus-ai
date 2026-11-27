"""Shared helpers for graph tests: a recording fake model and request serialisation."""

import json
from collections.abc import Sequence
from typing import Any

from app.llm.fake import FakeChatModel, _Plan
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import Field


class RecordingFake(FakeChatModel):
    """The scripted fake that also remembers every request it was sent."""

    requests: list[list[BaseMessage]] = Field(default_factory=list)

    def _plan(self, messages: Sequence[BaseMessage]) -> _Plan:
        self.requests.append(list(messages))
        return super()._plan(messages)


def wire(messages: Sequence[BaseMessage]) -> bytes:
    """The bytes that identify a message list to a prefix cache: role and text, in order."""
    return json.dumps([[m.type, m.text] for m in messages], ensure_ascii=False).encode()


def thread(thread_id: str = "conv-1", **configurable: Any) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id, "user_id": "u-1", **configurable}}


def filler(tokens: int) -> str:
    """Text of roughly `tokens` o200k tokens (a short common word repeated)."""
    return " ".join(["word"] * tokens)


class ToolFake(RecordingFake):
    """The recording fake that accepts `bind_tools` (the tool-capable graph binds its tools)."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ToolFake:
        return self
