"""Start of every run: reset the per-run fields.

Memories are fetched here once long-term memory exists (M5); until then the list is empty.
"""

from typing import Any

from app.graph.state import ChatState


async def load_context(state: ChatState) -> dict[str, Any]:
    return {"memories": [], "tool_calls_this_turn": 0}
