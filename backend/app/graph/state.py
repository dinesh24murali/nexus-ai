"""Graph state (docs/02_outputs/08 section 2.1), the v1 subset.

`route` (intent router, M7) joins when its feature lands; added keys are optional, so existing
checkpoints stay readable.
"""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# The most individual tool calls one turn may make (doc 08 section 2.2).
MAX_TOOL_CALLS = 6


class Citation(TypedDict):
    """A source the answer may cite as `[index]`; the same shape as the API's `Citation`."""

    index: int
    source_type: Literal["file", "web"]
    title: str
    file_id: str | None
    page: int | None
    url: str | None
    snippet: str


class ChatState(TypedDict):
    # Full turn history. Older turns are replaced by `summary` when it grows too long.
    messages: Annotated[list[AnyMessage], add_messages]
    # Rolling summary of the messages that were removed from `messages`. Lives in the checkpoint
    # only (there is no database column for it).
    summary: NotRequired[str | None]
    # Loaded per run by `load_context` and rendered into the late context message.
    memories: NotRequired[list[str]]
    # The user's most recent ready files, listed in the late context message so the model can
    # target one with `file_ids`. Reset by `load_context` on every run.
    recent_files: NotRequired[list[RecentFile]]
    # Sources gathered by tools in this run, numbered from 1. Reset by `load_context`; a plain
    # list (not an appending reducer) because a reducer cannot reset.
    citations: NotRequired[list[Citation]]
    # Individual tool calls made in the current turn, capped at MAX_TOOL_CALLS.
    tool_calls_this_turn: NotRequired[int]
    # The user's decisions on the tool calls that needed approval, by tool call id:
    # {"decision": "approve" | "edit" | "reject", "args": {...}}. Written by the `approval` node,
    # consumed (and cleared) by the `tools` node, reset by `load_context`.
    approvals: NotRequired[dict[str, dict[str, Any]]]
    # The turn's tool calls so far, in the API's `ToolCall` shape (id, name, args, status, summary).
    # A run that resumes after an approval stores the whole turn, and the events of the run before
    # it are gone, so the state keeps them. Reset by `load_context`.
    tool_log: NotRequired[list[dict[str, Any]]]


class RecentFile(TypedDict):
    id: str
    filename: str
    uploaded_at: str
