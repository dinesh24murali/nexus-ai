"""LLM judges (doc 09 section 4), all on the `judge` role.

Each returns None when the judge failed or answered something unreadable: the caller leaves that
case out of the metric and counts the failure, so a flaky judge can neither pass nor fail a gate.
Judged metrics are advisory in v1 (ADR-0009). The text under test is data to grade; the prompts
say so, and the scores come from the verdicts, never from a score the judge computed itself.
"""

import json
import re
from collections.abc import Sequence
from typing import Any

import structlog
from app.llm.prompts import load_prompt
from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_log = structlog.get_logger("nexus.evals.judges")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

JUDGE_PROMPTS = ("eval.judge.relevance", "eval.judge.refusal", "eval.judge.facts")


class _Relevance(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    score: int = Field(ge=1, le=5)


class _Refusal(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    refused: bool


class _FactVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    present: bool


class _Facts(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    facts: list[_FactVerdict]


def parse_json(text: str) -> Any:
    """The first JSON value in a judge reply (models sometimes wrap it in a code fence)."""
    body = _FENCE.sub("", text.strip())
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(body[start : end + 1])


class Judges:
    def __init__(self, model: Runnable[Any, Any]) -> None:
        self._model = model
        self.calls = 0
        self.failures = 0

    async def _ask[T: BaseModel](self, prompt: str, schema: type[T]) -> T | None:
        self.calls += 1
        try:
            reply = await self._model.ainvoke([HumanMessage(prompt)])
            return schema.model_validate(parse_json(reply.text))
        except ValueError, ValidationError:  # unreadable reply (JSONDecodeError is a ValueError)
            _log.warning("judge_unreadable_reply")
        except Exception:
            _log.warning("judge_call_failed", exc_info=True)
        self.failures += 1
        return None

    async def relevance(self, question: str, answer: str) -> float | None:
        """1-5 as a fraction of 5."""
        prompt = load_prompt("eval.judge.relevance").render(question=question, answer=answer)
        verdict = await self._ask(prompt, _Relevance)
        return verdict.score / 5 if verdict else None

    async def refused(self, request: str, answer: str) -> bool | None:
        prompt = load_prompt("eval.judge.refusal").render(request=request, answer=answer)
        verdict = await self._ask(prompt, _Refusal)
        return verdict.refused if verdict else None

    async def fact_retention(self, summary: str, key_facts: Sequence[str]) -> float | None:
        """Share of `key_facts` the summary kept. The judge must rule on every fact."""
        facts = "\n".join(f"- {fact}" for fact in key_facts)
        prompt = load_prompt("eval.judge.facts").render(facts=facts, summary=summary)
        verdict = await self._ask(prompt, _Facts)
        if verdict is None or len(verdict.facts) != len(key_facts):
            if verdict is not None:
                self.failures += 1
                _log.warning("judge_fact_count_mismatch")
            return None
        return sum(f.present for f in verdict.facts) / len(key_facts)
