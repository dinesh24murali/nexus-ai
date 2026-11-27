"""The `memory.e2e` step (doc 09 sections 3 and 9, TASK-039): memory by chat, from the user's side.

Each case runs through the REAL graph: the real router (for plain-text requests), the real
`manage_memory` pipeline over the REAL memory store on Postgres, and, in conversation B, the real
chat model reading the memories through `<user_memory>`. Nothing is faked except the user: every
case gets a fresh throwaway user id (the store is keyed by it, so cases cannot see each other) and
its memories are deleted at the end.

A slash command is parsed by the same code the API uses before the graph runs. A `forget_memory`
card is answered by the step (`decide`); the step also checks what no scoring should ever have to
trust: the card appeared BEFORE anything was deleted, and an approval deletes exactly the candidate
that was picked.

`memory.e2e_recall`: the share of cases where every given check holds (a pass or a fail per case).
A run that errored scores 0, so an outage cannot raise the metric.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.graph.commands import StaticReply, parse_command
from app.graph.intents import PIPELINES
from app.memory.store import MemoryStore, open_memory_store
from app.rag.embedder import Embedder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from evals.datasets import Dataset, MemoryE2ECase
from evals.run import CaseRecord, SuiteRun, _each

Graph = CompiledStateGraph[Any, Any, Any, Any]

CASE_TIMEOUT_S = 180.0
MAX_CARDS_PER_TURN = 2


@dataclass
class E2EEnv:
    """What the step needs from outside: the database the store lives in, an embedder for it, and
    `graph_for(user)`, the production wiring of the graph (a fresh checkpointer per call, so each
    conversation starts empty)."""

    database_url: str
    embedder: Embedder
    graph_for: Callable[[uuid.UUID], Graph]


@dataclass
class Conversation:
    graph: Graph
    user: uuid.UUID
    thread: str = field(default_factory=lambda: str(uuid.uuid4()))
    cards: list[list[dict[str, str]]] = field(default_factory=list)  # candidates of each card
    replies: list[str] = field(default_factory=list)


def _config(conv: Conversation, command: Any = None) -> RunnableConfig:
    configurable: dict[str, Any] = {
        "thread_id": conv.thread,
        "user_id": str(conv.user),
        "user_display_name": "Eval User",
        "today": "2026-11-22",
    }
    if command is not None:  # parsed by the same code as the API, before the graph runs
        configurable["command"] = {
            "name": command.name,
            "intent": command.intent,
            "slots": command.slots,
        }
    return {"configurable": configurable}


async def say(
    conv: Conversation, text: str, case: MemoryE2ECase, store: MemoryStore, problems: list[str]
) -> None:
    """One user message, handled the way the API would, with the forget card (if any) answered."""
    parsed = parse_command(text, available=PIPELINES)
    if isinstance(parsed, StaticReply):  # help or a usage reply: no graph
        conv.replies.append(parsed.text)
        return
    count_before = await store.count(conv.user)
    config = _config(conv, parsed)
    await conv.graph.ainvoke({"messages": [HumanMessage(text)]}, config)
    for _ in range(MAX_CARDS_PER_TURN):
        paused = (await conv.graph.aget_state(config)).interrupts
        if not paused:
            break
        card = paused[0].value
        candidates: list[dict[str, str]] = list(card.get("args", {}).get("candidates", []))
        conv.cards.append(candidates)
        if card.get("tool") != "forget_memory" or case.decide is None:
            problems.append("unexpected_card")
            break
        if await store.count(conv.user) != count_before:
            problems.append("deleted_before_the_card")  # the card came too late
        picked = (
            candidates[min(case.pick, len(candidates) - 1)]
            if case.decide == "approve" and candidates
            else None
        )
        answer: dict[str, Any] = (
            {"decision": "approve", "args": {"memory_id": picked["memory_id"]}}
            if picked
            else {"decision": "reject"}
        )
        await conv.graph.ainvoke(Command(resume=answer), config)
        remaining = [m.content for m in await store.all(conv.user)]
        if len(remaining) != count_before - (1 if picked else 0):
            problems.append("wrong_number_deleted")
        if picked is not None and picked["content"] in remaining:
            problems.append("picked_memory_still_there")
    state = (await conv.graph.aget_state(config)).values
    last = next((m for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage)), None)
    conv.replies.append(last.text if last is not None else "")


def _has(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


def check_store(case: MemoryE2ECase, texts: list[str]) -> list[str]:
    """Problems with what is stored after conversation A (names only, never the text)."""
    problems: list[str] = []
    for needle in case.expect_memories:
        if sum(_has(t, needle) for t in texts) != 1:
            problems.append("expected_memory_not_stored_exactly_once")
    if any(_has(t, needle) for needle in case.expect_absent for t in texts):
        problems.append("memory_that_should_be_gone_remains")
    if case.expect_count is not None and len(texts) != case.expect_count:
        problems.append("wrong_memory_count")
    return problems


def check_cards(case: MemoryE2ECase, cards: list[list[dict[str, str]]]) -> list[str]:
    """The first card must offer one candidate per expected substring (any order); `[]` expects
    no card at all."""
    if case.expect_candidates is None:
        return []
    if not case.expect_candidates:
        return [] if not cards else ["unexpected_card"]
    if not cards:
        return ["no_card"]
    remaining = [c["content"] for c in cards[0]]
    if len(remaining) != len(case.expect_candidates):
        return ["wrong_candidates"]
    for needle in case.expect_candidates:
        match = next((t for t in remaining if _has(t, needle)), None)
        if match is None:
            return ["wrong_candidates"]
        remaining.remove(match)
    return []


def check_texts(needles: list[str], text: str, name: str) -> list[str]:
    return [name] if any(not _has(text, n) for n in needles) else []


async def run_case(case: MemoryE2ECase, env: E2EEnv) -> tuple[list[str], str]:
    """(problems, the answer of conversation B or ''). Raises on an infrastructure error."""
    user = uuid.uuid4()
    problems: list[str] = []
    async with open_memory_store(env.database_url, env.embedder) as store:
        try:
            for text in case.existing_memories:
                await store.create(
                    user, content=text, category="fact", source="manual", conversation_id=None
                )
            a = Conversation(env.graph_for(user), user)
            for turn in case.turns_a:
                await say(a, turn, case, store, problems)
            problems += check_cards(case, a.cards)
            problems += check_texts(case.expect_in_replies, "\n".join(a.replies), "reply_missing")
            problems += check_store(case, [m.content for m in await store.all(user)])

            answer = ""
            if case.probe_b is not None:
                b = Conversation(env.graph_for(user), user)  # a new conversation, same user
                await say(b, case.probe_b, case, store, problems)
                answer = b.replies[-1] if b.replies else ""
                problems += check_texts(case.expect_in_answer, answer, "answer_missing")
            return problems, answer
        finally:
            await store.clear(user)  # the throwaway user leaves nothing behind


async def run_memory_e2e(suite: SuiteRun, dataset: Dataset[MemoryE2ECase], *, env: E2EEnv) -> None:
    async def work(case: MemoryE2ECase, index: int) -> None:
        problems: list[str] = []
        answer = ""
        error: str | None = None
        async with suite.gate:
            try:
                problems, answer = await asyncio.wait_for(
                    run_case(case, env), timeout=CASE_TIMEOUT_S
                )
            except Exception as exc:  # an outage or a crash is a miss, never an excluded case
                error = type(exc).__name__
        record = CaseRecord("memory.e2e", case.id, index, output=answer, error=error)
        record.notes["problems"] = problems  # codes only, never memory text
        record.scores["memory.e2e_recall"] = float(error is None and not problems)
        for key, value in record.scores.items():
            suite.scores.add(key, value)
        suite.record(record, case_input=case.turns_a)

    await _each(dataset.cases, suite.runs, work, parallel=False)
