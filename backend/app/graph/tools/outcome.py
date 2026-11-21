"""What a tool run hands back to the `tools` node, shared by every tool."""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import ValidationError

from app.graph.state import Citation


@dataclass(frozen=True)
class ToolOutcome:
    content: str  # the ToolMessage text the model reads
    status: Literal["completed", "failed", "timed_out"]
    summary: str  # short text for the client's tool_end event
    citations: list[Citation] = field(default_factory=list)
    embedding_tokens: int = 0  # billed for the query, recorded as spend by the run


class Pending(Protocol):
    """The result of a tool's first phase, before its sources are numbered: the `tools` node runs
    the phases of one message in parallel and then calls `render` in call order, so `[n]` numbers
    never depend on which search finished first."""

    def render(self, *, first_index: int) -> ToolOutcome: ...


def invalid_arguments(error: ValidationError) -> ToolOutcome:
    """Name the fields and the rules, never the submitted values."""
    problems = "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
    )
    return ToolOutcome(
        content=f"Invalid arguments: {problems}. Fix them and try once more.",
        status="failed",
        summary="invalid arguments",
    )
