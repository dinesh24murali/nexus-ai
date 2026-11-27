"""Stand-ins for the deep-research pipeline's tests: a pre-check that records its calls."""

import uuid


class StubResearch:
    """The `ResearchChecker`: allows every run unless `refusal` is set, and records each call."""

    def __init__(self) -> None:
        self.refusal: str | None = None
        self.calls: list[tuple[uuid.UUID, bool]] = []

    async def check(self, user_id: uuid.UUID, *, pdf: bool) -> str | None:
        self.calls.append((user_id, pdf))
        return self.refusal
