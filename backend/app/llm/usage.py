"""Token usage and cost of every model call in one run (docs/02_outputs/08 section 10).

A LangChain callback handler: attach it to the run config and it sees every LLM call, including the
summariser and fallbacks, so the message's `usage` covers "all LLM calls in the run" as the API spec
promises. A call that reports no usage is ignored; a call whose response names no model is billed to
`default_model`.
"""

from decimal import Decimal
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.llm.pricing import Usage, cost_usd, usage_from_metadata


class UsageCollector(BaseCallbackHandler):
    run_inline = True  # plain bookkeeping: no need to hop to a worker thread

    def __init__(self, default_model: str) -> None:
        self._default_model = default_model
        self.by_model: dict[str, Usage] = {}
        self.calls = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                meta = getattr(message, "usage_metadata", None)
                if not meta:
                    continue
                response_meta = getattr(message, "response_metadata", None) or {}
                model = str(response_meta.get("model_name") or self._default_model)
                added = usage_from_metadata(meta)
                previous = self.by_model.get(model, Usage(0, 0, 0))
                self.by_model[model] = Usage(
                    previous.prompt_tokens + added.prompt_tokens,
                    previous.cached_tokens + added.cached_tokens,
                    previous.completion_tokens + added.completion_tokens,
                )
                self.calls += 1

    @property
    def total(self) -> Usage:
        usages = self.by_model.values()
        return Usage(
            sum(u.prompt_tokens for u in usages),
            sum(u.cached_tokens for u in usages),
            sum(u.completion_tokens for u in usages),
        )

    @property
    def cost_usd(self) -> Decimal:
        return sum((cost_usd(m, u) for m, u in self.by_model.items()), Decimal(0))

    @property
    def main_model(self) -> str:
        """The model that wrote the most output: the answer, not a helper call."""
        if not self.by_model:
            return self._default_model
        return max(self.by_model, key=lambda m: self.by_model[m].completion_tokens)
