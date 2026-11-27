"""`manage_memory`: list, save and forget memories by chat (doc 08 section 2.5, TASK-039).

No chat model: the router (or a slash command) supplies the action and the text as slots, and the
replies are templates (`memory/ops.py`). Two nodes:

- `manage_memory` does everything that needs no confirmation. `list` reads, `save` goes through the
  same policy as the extraction job (`memory.policy`), `forget` only SEARCHES and puts the
  candidates into the state.
- `forget_confirm` asks. It calls `interrupt()` with the candidates before anything is deleted,
  and deletes exactly one memory, and only one of the candidates it showed. Anything that is not a
  clear approval of one of them (a reject, an unknown id, a malformed answer) deletes nothing.

Splitting the search from the confirmation matters: LangGraph runs a node again from its top when it
resumes, so the node that interrupts must have no side effect and no search before the interrupt.

Every manager call takes the run's trusted user id (never anything from model output), and memory
text is never logged.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END
from langgraph.types import interrupt

from app.graph.nodes.approval import APPROVAL_TTL
from app.graph.nodes.types import ChatNode
from app.graph.state import ChatState
from app.memory import ops
from app.memory.store import Memory

TOOL_NAME = "forget_memory"


@dataclass(frozen=True)
class SaveOutcome:
    status: Literal["created", "duplicate", "refused"]
    content: str | None = None  # the text as stored (created)
    reason: str | None = None  # why it was refused (a code)
    embedding_tokens: int = 0


@dataclass(frozen=True)
class Found:
    memories: list[Memory] = field(default_factory=list)  # closest first, with scores
    embedding_tokens: int = 0


class MemoryManager(Protocol):
    """What `manage_memory` may do to the user's memories. Production: `memory/manage.py`."""

    async def enabled(self, user_id: uuid.UUID) -> bool: ...
    async def list(self, user_id: uuid.UUID) -> list[Memory]: ...
    async def save(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID | None, content: str
    ) -> SaveOutcome: ...
    async def find(self, user_id: uuid.UUID, query: str) -> Found: ...
    async def forget(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> bool: ...


def _user_id(config: RunnableConfig) -> uuid.UUID:
    # The owner comes from the trusted run config, never from message content.
    return uuid.UUID(str(config["configurable"]["user_id"]))


def _conversation_id(config: RunnableConfig) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(config["configurable"].get("thread_id")))
    except ValueError:
        return None


def _reply(text: str) -> dict[str, Any]:
    return {"messages": [AIMessage(content=text)], "forget_candidates": []}


def _spend(tokens: int) -> None:
    if tokens:  # the query embedding is paid for; the run records it (the client never sees this)
        get_stream_writer()({"kind": "spend", "embedding_tokens": tokens})


def _updated(action: Literal["created", "deleted"], content: str) -> None:
    get_stream_writer()({"kind": "memory_updated", "action": action, "content": content})


def _writes_disabled(config: RunnableConfig) -> bool:
    return bool(config["configurable"].get("memory_writes_disabled"))


def make_manage_memory_node(manager: MemoryManager) -> ChatNode:
    async def manage_memory(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        user_id = _user_id(config)
        slots = (state.get("route") or {}).get("slots") or {}
        action = slots.get("memory_action")
        content = str(slots.get("memory_content") or "").strip()

        if not await manager.enabled(user_id):
            return _reply(ops.NOTICE_OFF)
        if action == "list":
            return _reply(ops.render_list(await manager.list(user_id)))
        if action not in ("save", "forget"):
            return _reply(ops.USAGE_SAVE)
        if _writes_disabled(config):
            return _reply(ops.NOTICE_WRITES_OFF)
        if action == "save":
            return await _save(manager, user_id, config, content)
        return await _find(manager, user_id, content)

    return manage_memory


async def _save(
    manager: MemoryManager, user_id: uuid.UUID, config: RunnableConfig, content: str
) -> dict[str, Any]:
    if not content:
        return _reply(ops.USAGE_SAVE)
    outcome = await manager.save(user_id, _conversation_id(config), content)
    _spend(outcome.embedding_tokens)
    if outcome.status == "created" and outcome.content:
        _updated("created", outcome.content)
        return _reply(ops.saved_reply(outcome.content))
    if outcome.status == "duplicate":
        return _reply(ops.duplicate_reply())
    return _reply(ops.refusal_reply(outcome.reason or "empty"))


async def _find(manager: MemoryManager, user_id: uuid.UUID, query: str) -> dict[str, Any]:
    if not query:
        return _reply(ops.USAGE_FORGET)
    found = await manager.find(user_id, query)
    _spend(found.embedding_tokens)
    candidates = ops.choose_candidates(found.memories)
    if not candidates:
        return _reply(ops.NOT_FOUND)
    return {
        "forget_candidates": [{"memory_id": str(m.id), "content": m.content} for m in candidates]
    }


def after_manage_memory(state: ChatState) -> str:
    """To the confirmation when there is something to forget, otherwise the turn is over."""
    return "forget_confirm" if state.get("forget_candidates") else END


def chosen_memory(answer: Any, candidates: list[dict[str, str]]) -> dict[str, str] | None:
    """The candidate the user approved, or None (which deletes nothing).

    Only a clear `approve` counts, and the id must be one of the candidates that were shown. With a
    single candidate the id may be left out; with several it must name one."""
    if not isinstance(answer, dict) or answer.get("decision") != "approve":
        return None
    args = answer.get("args")
    wanted = args.get("memory_id") if isinstance(args, dict) else None
    if wanted is None and len(candidates) == 1:
        return candidates[0]
    return next((c for c in candidates if c["memory_id"] == wanted), None)


def make_forget_confirm_node(manager: MemoryManager) -> ChatNode:
    async def forget_confirm(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
        candidates = list(state.get("forget_candidates") or [])
        now = datetime.now(UTC)
        # Nothing before this line has a side effect: the node runs again from the top on resume.
        answer = interrupt(
            {
                "interrupt_id": f"int_{uuid.uuid4().hex[:12]}",
                "tool": TOOL_NAME,
                "tool_call_id": f"forget_{uuid.uuid4().hex[:12]}",
                "args": {"candidates": candidates},
                "created_at": now.isoformat(),
                "expires_at": (now + APPROVAL_TTL).isoformat(),
            }
        )
        chosen = chosen_memory(answer, candidates)
        if chosen is None:
            return _reply(ops.KEPT)
        user_id = _user_id(config)
        # The switches are read per run, so one flipped while the card waited still holds.
        if _writes_disabled(config) or not await manager.enabled(user_id):
            return _reply(ops.NOTICE_WRITES_OFF)
        if not await manager.forget(user_id, uuid.UUID(chosen["memory_id"])):
            return _reply(ops.NOT_DELETED)
        _updated("deleted", chosen["content"])
        return _reply(ops.forgot_reply(chosen["content"]))

    return forget_confirm
