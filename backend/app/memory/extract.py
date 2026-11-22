"""Turning one conversation turn into memory operations (docs/02_outputs/08 section 7).

`plan_ops` asks the background model for create/update/delete operations (strict structured
output) and keeps only those that are safe to apply: at most 3, an `update` or `delete` only for a
memory the model was shown, every text through `memory.policy`. It touches no store, so the eval
suite can call it with the real model and no database. `apply_plan` writes a plan: a `create` that
is >= 0.9 similar to an existing memory is dropped, and the per-user cap is enforced.

Only the user's own message is a source of memories. The assistant's answer is given to the model
as context for what the user referred to, never as something to remember.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel, ConfigDict, ValidationError

from app.llm.prompts import Prompt, load_prompt
from app.memory import policy
from app.memory.store import CATEGORIES, MEMORY_CAP, MemoryStore

PROMPT_NAME = "nexus.memory_extract"
MAX_OPS = 3  # per turn
DUPLICATE_SIMILARITY = 0.9  # a create this close to an existing memory is dropped
SIMILAR_MEMORIES_SHOWN = 20  # existing memories given to the model
USER_TEXT_CHARS = 4_000
ASSISTANT_TEXT_CHARS = 2_000

Category = Literal["preference", "fact", "goal", "context"]


class MemoryOp(BaseModel):
    """One operation as the model returns it. Strict structured output needs every field present,
    so unused ones are null."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["create", "update", "delete"]
    id: str | None
    content: str | None
    category: Category | None


class MemoryOps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[MemoryOp]


class Remembered(Protocol):
    """What the prompt needs to know about an existing memory."""

    @property
    def id(self) -> uuid.UUID: ...
    @property
    def content(self) -> str: ...
    @property
    def category(self) -> str: ...


@dataclass(frozen=True)
class PlannedOp:
    op: Literal["create", "update", "delete"]
    memory_id: uuid.UUID | None  # update and delete
    content: str | None  # create and update, cleaned
    category: str | None


@dataclass(frozen=True)
class Plan:
    ops: list[PlannedOp] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # reason codes only, never text


def render_prompt(
    prompt: Prompt, user_text: str, assistant_text: str, existing: Sequence[Remembered]
) -> str:
    listing = (
        "\n".join(f"{m.id} | {m.category} | {policy.normalise(m.content)}" for m in existing)
        or "(none)"
    )
    return prompt.render(
        existing_memories=listing,
        user_message=user_text[:USER_TEXT_CHARS],
        assistant_message=assistant_text[:ASSISTANT_TEXT_CHARS],
    )


def _parse(raw: Any) -> MemoryOps:
    if isinstance(raw, MemoryOps):
        return raw
    return MemoryOps.model_validate(raw)


def filter_ops(ops: Sequence[MemoryOp], shown: Sequence[Remembered]) -> Plan:
    """The operations that may be applied, and why the others were dropped."""
    known = {m.id for m in shown}
    planned: list[PlannedOp] = []
    rejected: list[str] = []
    touched: set[uuid.UUID] = set()
    for op in ops:
        if len(planned) >= MAX_OPS:
            rejected.append("over_limit")
            continue
        memory_id: uuid.UUID | None = None
        if op.op != "create":
            try:
                memory_id = uuid.UUID(op.id or "")
            except ValueError:
                memory_id = None
            if memory_id is None or memory_id not in known:
                rejected.append("unknown_id")  # invented, or not one of the memories shown
                continue
            if memory_id in touched:
                rejected.append("repeated_id")
                continue
        content: str | None = None
        if op.op != "delete":
            content = policy.normalise(op.content or "")
            if refusal := policy.check(content):
                rejected.append(refusal.reason)
                continue
        category = op.category if op.category in CATEGORIES else None
        if op.op == "create":
            category = category or "fact"
        if memory_id is not None:
            touched.add(memory_id)
        planned.append(PlannedOp(op.op, memory_id, content, category))
    return Plan(planned, rejected)


async def plan_ops(
    model: Runnable[Any, Any],
    user_text: str,
    assistant_text: str,
    existing: Sequence[Remembered],
    *,
    config: RunnableConfig | None = None,
    prompt: Prompt | None = None,
) -> Plan:
    """Ask the model what to change and keep what is safe. Raises if the reply is not valid
    structured output (the job then retries)."""
    prompt = prompt or load_prompt(PROMPT_NAME)
    text = render_prompt(prompt, user_text, assistant_text, existing)
    # The factory returns a Runnable; for this role it is a chat model (no fallback is configured).
    structured = cast("Any", model).with_structured_output(
        MemoryOps, method="json_schema", strict=True
    )
    raw = await structured.ainvoke(text, config)
    try:
        ops = _parse(raw)
    except ValidationError as exc:
        raise ValueError("the model's memory operations were not valid") from exc  # no text
    return filter_ops(ops.ops, existing)


@dataclass
class Applied:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    duplicates: int = 0  # creates dropped as near-copies
    missing: int = 0  # updates and deletes whose memory was already gone
    evicted: int = 0


async def apply_plan(
    store: MemoryStore,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    plan: Plan,
    *,
    cap: int = MEMORY_CAP,
    source: str = "extracted",
) -> Applied:
    done = Applied()
    for op in plan.ops:
        if op.op == "create":
            assert op.content is not None  # noqa: S101 - filter_ops guarantees it
            nearest = await store.search(user_id, op.content, limit=1)
            if nearest and (nearest[0].score or 0.0) >= DUPLICATE_SIMILARITY:
                done.duplicates += 1
                continue
            await store.create(
                user_id,
                content=op.content,
                category=op.category or "fact",
                source=source,
                conversation_id=conversation_id,
            )
            done.created += 1
        elif op.op == "update":
            assert op.memory_id is not None and op.content is not None  # noqa: S101
            if await store.update(user_id, op.memory_id, content=op.content, category=op.category):
                done.updated += 1
            else:
                done.missing += 1
        else:
            assert op.memory_id is not None  # noqa: S101
            if await store.delete(user_id, op.memory_id):
                done.deleted += 1
            else:
                done.missing += 1
    if done.created:
        done.evicted = await store.evict_to(user_id, cap)
    return done
