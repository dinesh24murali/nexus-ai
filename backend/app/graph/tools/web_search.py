"""`web_search` (doc 08 section 5): the public web through Tavily, as untrusted data.

Every call is approved by the user before it runs (`graph/policy.py`), and only the approved query
leaves the system: Tavily gets the query and the API key, nothing about the user (LLM-T07). Results
come back as numbered `<web_result>` blocks whose text and attributes are escaped, so a page can
never close its block or open another one (LLM-T02); only `http(s)` pages are kept, and nothing is
fetched from them in v1. Failures become error results that never carry exception text (it could
name hosts or include the key).
"""

import html
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.graph.state import Citation
from app.graph.tools.outcome import ToolOutcome, invalid_arguments

TOOL_NAME = "web_search"
MAX_QUERY_CHARS = 400
MAX_RESULTS = 5
MAX_RESULT_CHARS = 800
SNIPPET_CHARS = 300
TIMEOUT_S = 10.0  # doc 08 section 5
TAVILY_URL = "https://api.tavily.com/search"
NO_RESULTS = "No web results were found for that query."
_FAILED = "The web search failed. Answer without it, and say that you could not search the web."
_TIMED_OUT = "The web search timed out. Answer without it, and say that you could not search."


class WebSearchError(Exception):
    """The search provider failed. The message is safe to log: status codes only."""


class WebSearchTimeout(WebSearchError):
    pass


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    domain: str
    content: str


class WebSearcher(Protocol):
    async def search(self, query: str) -> list[WebResult]: ...


def _domain(url: str) -> str | None:
    """The host of an http(s) URL without a leading `www.`; None for anything else."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    host = host.lower()
    return host.removeprefix("www.")


def _parse(entry: Any) -> WebResult | None:
    if not isinstance(entry, dict):
        return None
    url = entry.get("url")
    domain = _domain(url) if isinstance(url, str) else None
    if domain is None or not isinstance(url, str):
        return None
    title = entry.get("title")
    content = entry.get("content")
    return WebResult(
        title=(title.strip() if isinstance(title, str) else "") or domain,
        url=url,
        domain=domain,
        content=(content if isinstance(content, str) else "").strip()[:MAX_RESULT_CHARS],
    )


class TavilySearcher:
    """Tavily's search API. One short-lived client per call (calls are rare: each is approved)."""

    def __init__(
        self,
        api_key: str | SecretStr,
        *,
        timeout_s: float = TIMEOUT_S,
        url: str = TAVILY_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        self._timeout_s = timeout_s
        self._url = url
        self._transport = transport

    def __repr__(self) -> str:
        return "TavilySearcher()"

    async def search(self, query: str) -> list[WebResult]:
        body = {
            "query": query,
            "max_results": MAX_RESULTS,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        headers = {"Authorization": f"Bearer {self._key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException:
            raise WebSearchTimeout("the search provider timed out") from None
        except httpx.HTTPError:
            raise WebSearchError("the search provider could not be reached") from None
        if response.status_code != 200:
            raise WebSearchError(f"the search provider answered HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise WebSearchError("the search provider sent an unreadable response") from None
        if not isinstance(data, dict):
            raise WebSearchError("the search provider sent an unexpected response")
        entries = data.get("results", [])
        if not isinstance(entries, list):
            raise WebSearchError("the search provider sent an unexpected response")
        parsed = (_parse(entry) for entry in entries)
        return [result for result in parsed if result is not None][:MAX_RESULTS]


class WebSearchArgs(BaseModel):
    """Arguments the model may pass. Unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(
        min_length=1,
        max_length=MAX_QUERY_CHARS,
        description="A short web search query. Do not include personal data.",
    )


def tool_spec() -> StructuredTool:
    """The tool definition bound to the chat model; the graph's `tools` node runs the search
    after the user approved the query."""

    def unused(**_: Any) -> str:
        raise RuntimeError("web_search runs in the tools node")

    return StructuredTool.from_function(
        func=unused,
        name=TOOL_NAME,
        description=(
            "Search the public web for current information. Use for recent events, prices, "
            "versions or anything after your knowledge cutoff, or when the user asks to search "
            "online. The user must approve every search. Returns numbered results; "
            "cite them as [n]."
        ),
        args_schema=WebSearchArgs,
    )


@dataclass(frozen=True)
class WebSearched:
    """The result of the search phase: results not yet numbered."""

    results: list[WebResult]

    def render(self, *, first_index: int) -> ToolOutcome:
        if not self.results:
            return ToolOutcome(NO_RESULTS, "completed", "no results found")
        citations: list[Citation] = [
            {
                "index": first_index + i,
                "source_type": "web",
                "title": r.title or r.domain,
                "file_id": None,
                "page": None,
                "url": r.url,
                "snippet": _snippet(r.content),
            }
            for i, r in enumerate(self.results)
        ]
        count = len(self.results)
        return ToolOutcome(
            content="\n".join(_block(first_index + i, r) for i, r in enumerate(self.results)),
            status="completed",
            summary=f"{count} result{'s' if count != 1 else ''} found",
            citations=citations,
        )


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1].rstrip() + "…"


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def _block(index: int, result: WebResult) -> str:
    attrs = (
        f'index="{index}" url="{_attr(result.url)}" domain="{_attr(result.domain)}" '
        f'title="{_attr(result.title or result.domain)}"'
    )
    return f"<web_result {attrs}>\n{html.escape(result.content, quote=False)}\n</web_result>"


async def web_search_phase(
    args: dict[str, Any], *, searcher: WebSearcher
) -> WebSearched | ToolOutcome:
    """Validate the arguments and search. Returns a `ToolOutcome` for every way it can go wrong.
    The `[n]` numbers are given later by `render`, in call order."""
    try:
        parsed = WebSearchArgs.model_validate(args)
    except ValidationError as error:
        return invalid_arguments(error)
    try:
        return WebSearched(await searcher.search(parsed.query))
    except WebSearchTimeout:
        return ToolOutcome(_TIMED_OUT, "timed_out", "timed out")
    except WebSearchError:
        return ToolOutcome(_FAILED, "failed", "search failed")


async def run_web_search(
    args: dict[str, Any], *, searcher: WebSearcher, first_index: int
) -> ToolOutcome:
    searched = await web_search_phase(args, searcher=searcher)
    if isinstance(searched, ToolOutcome):
        return searched
    return searched.render(first_index=first_index)
