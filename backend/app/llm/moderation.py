"""Input moderation before a chat run (docs/02_outputs/08 section 3, threat model LLM-T14).

Flagged text is refused with 400 `MODERATION_FLAGGED` and never stored. The check **fails open**:
if the moderation API is slow, down, unreadable or not configured, the message is allowed and the
skip is logged (and tagged `moderation:skipped` on the run's trace). That is deliberate: users are
invite-only, so availability beats strictness. The log carries the error type and the flagged
category names, never the text.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.config import Settings
from app.core.errors import ApiError
from app.llm.factory import MODERATION_TIMEOUT_S, build_moderation_client

_log = structlog.get_logger("nexus.moderation")

# The SDK already limits one attempt to 5 s and retries once; this caps the whole call.
TOTAL_TIMEOUT_S = 2 * MODERATION_TIMEOUT_S + 1


@dataclass(frozen=True)
class ModerationResult:
    flagged: bool
    categories: tuple[str, ...] = ()
    skipped: bool = False  # the text could not be checked (fail-open)


def moderation_flagged() -> ApiError:
    return ApiError(
        400,
        "MODERATION_FLAGGED",
        "This message was not sent because it may violate the content policy. "
        "Please rephrase it and try again.",
    )


def _category_names(categories: Any) -> tuple[str, ...]:
    data = (
        categories.model_dump(by_alias=True)
        if hasattr(categories, "model_dump")
        else dict(categories)
    )
    return tuple(sorted(name for name, hit in data.items() if hit is True))


class Moderator:
    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        timeout_s: float = TOTAL_TIMEOUT_S,
    ) -> None:
        self._settings = settings
        self._client = client
        self._timeout_s = timeout_s

    async def check(self, text: str) -> ModerationResult:
        try:
            if self._client is None:  # built on first use: a missing key must not stop the API
                self._client = build_moderation_client(self._settings)
            async with asyncio.timeout(self._timeout_s):
                response = await self._client.moderations.create(
                    model=self._settings.llm_moderation_model, input=text
                )
            result = response.results[0]
            if result.flagged:
                categories = _category_names(result.categories)
                _log.info("moderation_flagged", categories=list(categories))
                return ModerationResult(flagged=True, categories=categories)
            return ModerationResult(flagged=False)
        except Exception as exc:
            _log.warning("moderation_skipped", error_type=type(exc).__name__)
            return ModerationResult(flagged=False, skipped=True)
