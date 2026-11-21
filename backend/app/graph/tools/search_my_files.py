"""`search_my_files` (doc 08 section 5): passages from the caller's own files, as untrusted data.

The model gets numbered `<document>` blocks. Their text is escaped so a passage can never close
its tag or open another one, and the tool's description says the passages are data. The owner is
the `user_id` the graph was started with, never an argument (LLM-T05).
"""

import html
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.graph.state import Citation
from app.rag.retriever import Passage, Searcher

TOOL_NAME = "search_my_files"
MAX_QUERY_CHARS = 400
MAX_FILE_IDS = 10
SNIPPET_CHARS = 300
NOTHING_FOUND = "No matching passages were found in the user's files."


class SearchMyFilesArgs(BaseModel):
    """Arguments the model may pass. Unknown keys (such as a `user_id`) are ignored."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(
        min_length=1,
        max_length=MAX_QUERY_CHARS,
        description="What to look for, phrased as a short search query.",
    )
    file_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=MAX_FILE_IDS,
        description="Only search these files (ids from the recent files list or earlier results).",
    )


def tool_spec() -> StructuredTool:
    """The tool definition bound to the chat model. It is never invoked through LangChain: the
    graph's `tools` node runs `run_search_my_files` so it can enforce the cap and emit events."""

    def unused(**_: Any) -> str:
        raise RuntimeError("search_my_files runs in the tools node")

    return StructuredTool.from_function(
        func=unused,
        name=TOOL_NAME,
        description=(
            "Search the user's uploaded files. Use for questions about their documents. "
            "Returns numbered passages; cite them as [n]."
        ),
        args_schema=SearchMyFilesArgs,
    )


@dataclass(frozen=True)
class ToolOutcome:
    content: str  # the ToolMessage text the model reads
    status: Literal["completed", "failed", "timed_out"]
    summary: str  # short text for the client's tool_end event
    citations: list[Citation] = field(default_factory=list)
    embedding_tokens: int = 0  # billed for the query, recorded as spend by the run


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1].rstrip() + "…"


def _block(index: int, passage: Passage) -> str:
    attrs = f'index="{index}" file_id="{passage.file_id}" title="{html.escape(passage.filename)}"'
    if passage.page is not None:
        attrs += f' page="{passage.page}"'
    return f"<document {attrs}>\n{html.escape(passage.text, quote=False)}\n</document>"


def _invalid(error: ValidationError) -> ToolOutcome:
    # Name the fields and the rules, never the submitted values.
    problems = "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
    )
    return ToolOutcome(
        content=f"Invalid arguments: {problems}. Fix them and try once more.",
        status="failed",
        summary="invalid arguments",
    )


@dataclass(frozen=True)
class Searched:
    """The result of the search phase: passages not yet numbered."""

    passages: list[Passage]
    embedding_tokens: int


async def search_phase(
    args: dict[str, Any], *, retriever: Searcher, user_id: uuid.UUID
) -> Searched | ToolOutcome:
    """Validate the arguments and query the index. Returns a `ToolOutcome` when the arguments
    are invalid. The `[n]` numbers are given later by `render`, in call order, so searches of one
    parallel batch can run at the same time."""
    try:
        parsed = SearchMyFilesArgs.model_validate(args)
    except ValidationError as error:
        return _invalid(error)
    found = await retriever.search(user_id, parsed.query, parsed.file_ids)
    return Searched(passages=found.passages, embedding_tokens=found.embedding_tokens)


def render(searched: Searched, *, first_index: int) -> ToolOutcome:
    if not searched.passages:
        return ToolOutcome(
            content=NOTHING_FOUND,
            status="completed",
            summary="no passages found",
            embedding_tokens=searched.embedding_tokens,
        )
    passages = searched.passages
    citations: list[Citation] = [
        {
            "index": first_index + i,
            "source_type": "file",
            "title": p.filename,
            "file_id": str(p.file_id),
            "page": p.page,
            "url": None,
            "snippet": _snippet(p.text),
        }
        for i, p in enumerate(passages)
    ]
    count = len(passages)
    return ToolOutcome(
        content="\n".join(_block(first_index + i, p) for i, p in enumerate(passages)),
        status="completed",
        summary=f"{count} passage{'s' if count != 1 else ''} found",
        citations=citations,
        embedding_tokens=searched.embedding_tokens,
    )


async def run_search_my_files(
    args: dict[str, Any], *, retriever: Searcher, user_id: uuid.UUID, first_index: int
) -> ToolOutcome:
    searched = await search_phase(args, retriever=retriever, user_id=user_id)
    if isinstance(searched, ToolOutcome):
        return searched
    return render(searched, first_index=first_index)
