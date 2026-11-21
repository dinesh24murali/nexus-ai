"""Graph state (docs/02_outputs/08 section 2.1), the v1 subset.

`citations` (tools, M3) and `route` (intent router, M7) join when their features land; added keys
are optional, so existing checkpoints stay readable.
"""

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    # Full turn history. Older turns are replaced by `summary` when it grows too long.
    messages: Annotated[list[AnyMessage], add_messages]
    # Rolling summary of the messages that were removed from `messages`. Lives in the checkpoint
    # only (there is no database column for it).
    summary: NotRequired[str | None]
    # Loaded per run by `load_context` and rendered into the late context message.
    memories: NotRequired[list[str]]
    # Individual tool calls made in the current turn (capped at 6 when tools arrive).
    tool_calls_this_turn: NotRequired[int]
