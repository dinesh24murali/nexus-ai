"""Helpers for run-service tests: scripted models and an event collector."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from app.api.sse import Event
from app.graph.builder import build_graph
from app.llm.fake import FakeChatModel, _Plan
from app.services.runs import Run, RunService
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
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


@dataclass
class Env:
    service: RunService
    saver: AsyncPostgresSaver
    db: psycopg.Connection[Any]
    user: UUID
    cid: UUID
    overrides: list[str | None]

    def messages(self) -> list[tuple[str, str, str, str | None]]:
        rows = self.db.execute(
            "SELECT role, content, status, finish_reason FROM messages"
            " WHERE conversation_id = %s ORDER BY created_at, id",
            (self.cid,),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def lease(self) -> tuple[Any, Any]:
        row = self.db.execute(
            "SELECT active_run_id, run_heartbeat_at FROM conversations WHERE id = %s", (self.cid,)
        ).fetchone()
        assert row
        return row[0], row[1]

    async def thread(self) -> list[tuple[str, str]]:
        graph = build_graph(
            checkpointer=self.saver, chat_model=FakeChatModel(), summarize_model=FakeChatModel()
        )
        state = await graph.aget_state({"configurable": {"thread_id": str(self.cid)}})
        return [(m.type, m.text) for m in state.values.get("messages", [])]

    async def send(self, text: str, *, user: UUID | None = None) -> Run:
        return await self.service.start_send(user or self.user, "Marco", self.cid, text)

    async def resume(
        self,
        interrupt_id: str,
        decision: str,
        edited_args: dict[str, Any] | None = None,
        *,
        user: UUID | None = None,
    ) -> Run:
        return await self.service.start_resume(
            user or self.user, "Marco", self.cid, interrupt_id, decision, edited_args
        )

    async def state(self) -> Any:
        """The thread's state as the service's own graph sees it (so `next` and interrupts show)."""
        graph = self.service._graph(None)
        return await graph.aget_state({"configurable": {"thread_id": str(self.cid)}})

    def pending(self) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT pending_interrupt FROM conversations WHERE id = %s", (self.cid,)
        ).fetchone()
        assert row
        value: dict[str, Any] | None = row[0]
        return value

    def usage_today(self) -> tuple[int, int]:
        """(requests, prompt tokens) summed over the day's usage rows of the user."""
        row = self.db.execute(
            "SELECT COALESCE(SUM(requests), 0), COALESCE(SUM(prompt_tokens), 0)"
            " FROM usage_daily WHERE user_id = %s",
            (self.user,),
        ).fetchone()
        assert row
        return int(row[0]), int(row[1])
