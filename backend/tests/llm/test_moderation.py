"""Input moderation: flagged text, and fail-open on every kind of failure (threat model LLM-T14)."""

import asyncio
from typing import Any

import openai
import pytest
import structlog
from app.core.config import Settings
from app.core.errors import ApiError
from app.llm.factory import LLMConfigError, build_moderation_client
from app.llm.fake import FakeModerationClient
from app.llm.moderation import Moderator, moderation_flagged
from pydantic import BaseModel


class _Categories(BaseModel):
    violence: bool = False
    harassment: bool = False
    self_harm: bool = False


class _Result(BaseModel):
    flagged: bool
    categories: _Categories


class _Response(BaseModel):
    results: list[_Result]


class StubModerations:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if callable(self.outcome):
            return await self.outcome()
        return self.outcome


class StubClient:
    def __init__(self, outcome: Any) -> None:
        self.moderations = StubModerations(outcome)


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "app_env": "test", "llm_mode": "fake"}
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def moderator(outcome: Any, **kwargs: Any) -> tuple[Moderator, StubClient]:
    client = StubClient(outcome)
    return Moderator(settings(), client=client, **kwargs), client


async def test_clean_text_passes() -> None:
    ok = _Response(results=[_Result(flagged=False, categories=_Categories())])
    mod, client = moderator(ok)

    result = await mod.check("hello there")

    assert (result.flagged, result.skipped, result.categories) == (False, False, ())
    call = client.moderations.calls[0]
    assert call == {"model": "omni-moderation-latest", "input": "hello there"}


async def test_flagged_text_reports_the_categories_that_fired() -> None:
    bad = _Response(
        results=[
            _Result(flagged=True, categories=_Categories(violence=True, harassment=True)),
        ]
    )
    mod, _ = moderator(bad)

    result = await mod.check("something awful")

    assert result.flagged is True
    assert result.categories == ("harassment", "violence")  # sorted, only the true ones


async def test_the_configured_moderation_model_is_used() -> None:
    ok = _Response(results=[_Result(flagged=False, categories=_Categories())])
    client = StubClient(ok)
    mod = Moderator(settings(llm_moderation_model="omni-moderation-2099"), client=client)

    await mod.check("hi")

    assert client.moderations.calls[0]["model"] == "omni-moderation-2099"


@pytest.mark.parametrize(
    "failure",
    [
        openai.APITimeoutError(request=None),  # type: ignore[arg-type]
        openai.APIConnectionError(request=None),  # type: ignore[arg-type]
        RuntimeError("boom"),
        ValueError("a response we cannot read"),
    ],
)
async def test_any_failure_fails_open_and_is_logged_without_the_text(failure: Exception) -> None:
    mod, _ = moderator(failure)

    with structlog.testing.capture_logs() as logs:
        result = await mod.check("my secret message")

    assert (result.flagged, result.skipped) == (False, True)
    entry = next(e for e in logs if e["event"] == "moderation_skipped")
    assert entry["log_level"] == "warning"
    assert entry["error_type"] == type(failure).__name__
    assert "my secret message" not in repr(logs)


async def test_an_empty_result_list_fails_open() -> None:
    mod, _ = moderator(_Response(results=[]))

    result = await mod.check("hi")

    assert (result.flagged, result.skipped) == (False, True)


async def test_a_hung_moderation_api_does_not_hold_the_request() -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    mod, _ = moderator(hang, timeout_s=0.05)

    async with asyncio.timeout(2):
        result = await mod.check("hi")

    assert (result.flagged, result.skipped) == (False, True)


async def test_a_missing_api_key_fails_open_instead_of_breaking_chat() -> None:
    real_without_key = settings(llm_mode="real", openai_api_key="")
    mod = Moderator(real_without_key)

    result = await mod.check("hi")

    assert (result.flagged, result.skipped) == (False, True)


def test_the_refusal_is_a_400_that_does_not_echo_the_message() -> None:
    err = moderation_flagged()
    assert (err.status_code, err.code) == (400, "MODERATION_FLAGGED")
    assert err.details is None
    assert isinstance(err, ApiError)


# ---------------------------------------------------------------- the fake client (LLM_MODE=fake)


async def test_the_fake_client_flags_only_on_the_marker() -> None:
    client = FakeModerationClient()

    clean = await client.moderations.create(model="m", input="hello")
    flagged = await client.moderations.create(model="m", input="x [[moderation:flag]] y")

    assert clean.results[0].flagged is False
    assert flagged.results[0].flagged is True


async def test_the_fake_client_can_simulate_an_outage() -> None:
    mod = Moderator(settings(), client=FakeModerationClient())

    result = await mod.check("please [[moderation:down]]")

    assert (result.flagged, result.skipped) == (False, True)


async def test_the_fake_client_works_through_the_moderator() -> None:
    mod = Moderator(settings())  # fake mode: the factory supplies the fake client

    assert (await mod.check("[[moderation:flag]]")).flagged is True
    assert (await mod.check("fine")).flagged is False


# ---------------------------------------------------------------- the factory


def test_fake_mode_builds_the_fake_client() -> None:
    assert isinstance(build_moderation_client(settings()), FakeModerationClient)


def test_real_mode_builds_a_client_with_a_short_timeout_and_one_retry() -> None:
    client = build_moderation_client(
        settings(llm_mode="real", openai_api_key="sk-test-not-a-real-key-0123456789abcdef")
    )

    assert isinstance(client, openai.AsyncOpenAI)
    assert client.timeout == 5
    assert client.max_retries == 1


def test_real_mode_without_a_key_is_a_config_error() -> None:
    with pytest.raises(LLMConfigError):
        build_moderation_client(settings(llm_mode="real", openai_api_key=""))
