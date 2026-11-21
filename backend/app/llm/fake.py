"""Scripted fake chat model for `LLM_MODE=fake` (ADR-0005).

Used by unit tests, the Playwright e2e suite and load tests; refused in production by
`Settings`. The script for a call is chosen by a `[[fixture:<id>]]` marker in the newest human
message (default: echo the message). Which turn of the script to play is derived from the message
history (AI messages since the newest human message), never from instance state, so tool loops,
approvals, resumes and fresh instances all behave the same.

Approval interrupts are graph-level, not model-level: a fixture triggers one by returning a
tool call for a tool that requires approval (e.g. `web_search`).
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from functools import cache
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.messages.tool import ToolCallChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import BaseModel, ConfigDict, Field

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_MARKER = re.compile(r"\[\[fixture:([^\]]*)\]\]")
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_WORD = re.compile(r"\S+\s*")

SUMMARY_TEXT = "FAKE-SUMMARY"
TITLE_TEXT = "Fake title"


class FakeModelError(Exception):
    pass


class ScriptedCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class Turn(BaseModel):
    text: str = ""
    tool_calls: list[ScriptedCall] = Field(default_factory=list)


class Fixture(BaseModel):
    id: str
    turns: list[Turn]


@cache
def _load(fixture_id: str, directory: Path) -> Fixture:
    path = directory / f"{fixture_id}.json"
    if not path.is_file():
        raise FakeModelError(f"unknown fixture: {fixture_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    fixture = Fixture(id=fixture_id, turns=data.get("turns", []))
    if not fixture.turns:
        raise FakeModelError(f"fixture {fixture_id}: needs at least one of turns")
    if fixture.turns[-1].tool_calls:
        raise FakeModelError(f"fixture {fixture_id}: the last turn must not call tools")
    return fixture


def load_fixture(fixture_id: str, *, directory: Path | None = None) -> Fixture:
    if not _ID.match(fixture_id):
        raise FakeModelError(f"invalid fixture id: {fixture_id!r}")
    return _load(fixture_id, directory or FIXTURES_DIR)


def _text_of(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block if isinstance(block, str) else str(block.get("text", "")) for block in content
    )


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class _Plan(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int

    @property
    def usage(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


class FakeChatModel(BaseChatModel):
    role: str = "chat"
    token_delay_s: float = 0.0
    fixtures_dir: Path = FIXTURES_DIR

    @property
    def _llm_type(self) -> str:
        return "nexus-fake"

    # ---- scripting ---------------------------------------------------------------------------

    def _plan(self, messages: Sequence[BaseMessage]) -> _Plan:
        input_tokens = sum(_approx_tokens(_text_of(m)) for m in messages)
        if self.role == "summarize":
            return self._plain(SUMMARY_TEXT, input_tokens)
        if self.role == "title":
            return self._plain(TITLE_TEXT, input_tokens)

        last_human = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
        )
        newest = _text_of(messages[last_human]) if last_human >= 0 else ""
        marker = _MARKER.search(newest)
        if not marker:
            return self._plain(f"Fake reply: {newest}", input_tokens)

        fixture = load_fixture(marker.group(1), directory=self.fixtures_dir)
        turn_index = sum(1 for m in messages[last_human + 1 :] if isinstance(m, AIMessage))
        turn = fixture.turns[min(turn_index, len(fixture.turns) - 1)]
        calls = [
            {
                "name": call.name,
                "args": call.args,
                "id": f"call_{fixture.id}_{turn_index}_{i}",
                "type": "tool_call",
            }
            for i, call in enumerate(turn.tool_calls)
        ]
        output_tokens = _approx_tokens(turn.text) + sum(
            _approx_tokens(json.dumps(c["args"])) for c in calls
        )
        return _Plan(
            text=turn.text, tool_calls=calls, input_tokens=input_tokens, output_tokens=output_tokens
        )

    @staticmethod
    def _plain(text: str, input_tokens: int) -> _Plan:
        return _Plan(
            text=text,
            tool_calls=[],
            input_tokens=input_tokens,
            output_tokens=_approx_tokens(text),
        )

    def _chunks(self, plan: _Plan) -> list[AIMessageChunk]:
        chunks = [AIMessageChunk(content=word) for word in _WORD.findall(plan.text)]
        if plan.tool_calls:
            chunks.append(
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        ToolCallChunk(
                            name=call["name"],
                            args=json.dumps(call["args"]),
                            id=call["id"],
                            index=i,
                        )
                        for i, call in enumerate(plan.tool_calls)
                    ],
                )
            )
        if not chunks:
            chunks.append(AIMessageChunk(content=""))
        last = chunks[-1]
        chunks[-1] = AIMessageChunk(
            content=last.content,
            tool_call_chunks=last.tool_call_chunks,
            usage_metadata=plan.usage,
            response_metadata={"finish_reason": "tool_calls" if plan.tool_calls else "stop"},
        )
        return chunks

    # ---- BaseChatModel -----------------------------------------------------------------------

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        plan = self._plan(messages)
        message = AIMessage(
            content=plan.text,
            tool_calls=plan.tool_calls,
            usage_metadata=plan.usage,
            response_metadata={"finish_reason": "tool_calls" if plan.tool_calls else "stop"},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for i, chunk in enumerate(self._chunks(self._plan(messages))):
            if i and self.token_delay_s:
                time.sleep(self.token_delay_s)
            yield ChatGenerationChunk(message=chunk)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for i, chunk in enumerate(self._chunks(self._plan(messages))):
            if i and self.token_delay_s:
                await asyncio.sleep(self.token_delay_s)
            if run_manager:
                await run_manager.on_llm_new_token(str(chunk.content), chunk=None)
            yield ChatGenerationChunk(message=chunk)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeChatModel:
        return self
