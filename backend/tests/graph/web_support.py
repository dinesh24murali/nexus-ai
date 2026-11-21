"""A stand-in web searcher for graph tests: scripted results, and a record of every query."""

from dataclasses import dataclass, field

from app.graph.tools.web_search import WebResult


def web_result(n: int = 1, content: str = "Some content") -> WebResult:
    return WebResult(f"Title {n}", f"https://example{n}.com/a", f"example{n}.com", content)


@dataclass
class StubWeb:
    results: list[WebResult] = field(default_factory=lambda: [web_result(1)])
    queries: list[str] = field(default_factory=list)

    async def search(self, query: str) -> list[WebResult]:
        self.queries.append(query)
        return list(self.results)
