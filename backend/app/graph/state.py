"""Graph state (docs/02_outputs/08 section 2.1), the v1 subset.

Keys added later (`route`, `forget_candidates`, `document_*`) are optional, so existing
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
    # The pipeline chosen for the newest message (`graph/router.py::Route.as_state`): intent,
    # source (router | command | fallback), confidence, slots, reason. Written by `router` on
    # every new message, kept across an approval pause (a resume never re-routes), and read back
    # as the "previous intent" of the next turn.
    route: NotRequired[dict[str, Any]]
    # `manage_memory` forget: the memories (id and text, best match first) offered to the user in
    # the confirmation card. Written by `manage_memory`, read by `forget_confirm`.
    forget_candidates: NotRequired[list[dict[str, str]]]
    # `generate_document` (TASK-041). All five are reset by `document_check` at the start of every
    # document turn: the checkpoint keeps them, and a stale draft would skip the model.
    # `document_stop`: a node ended the turn early with a reply; `document_material`: what the model
    # composes from (data, not instructions); `document_sources`: the numbered web results of a
    # brief; `document_draft`: the `DocumentDraft` as a dict; `document_kind`: the documents.kind.
    document_stop: NotRequired[bool]
    document_material: NotRequired[str]
    document_sources: NotRequired[list[Citation]]
    document_draft: NotRequired[dict[str, Any] | None]
    document_kind: NotRequired[str | None]
    # `deep_research` (TASK-043). All of them are reset by `research_check` at the start of every
    # research turn (the checkpoint keeps them). `research_stop`: a node ended the turn early with
    # a reply; `research_queries`: the planner's queries, shown to the user; `research_approved`:
    # what the user approved (the ONLY queries that are searched); `research_material`: the numbered
    # `<web_result>` blocks the report is written from (data, not instructions);
    # `research_sources`: the same results as numbered citations.
    research_stop: NotRequired[bool]
    research_queries: NotRequired[list[str]]
    research_approved: NotRequired[list[str]]
    research_material: NotRequired[str]
    research_sources: NotRequired[list[Citation]]


class RecentFile(TypedDict):
    id: str
    filename: str
    uploaded_at: str
