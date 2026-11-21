import pytest
from app.core.config import Settings
from app.llm.factory import LLMConfigError, get_model
from app.llm.fake import FakeChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableBinding, RunnableWithFallbacks
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

ROLES = ["chat", "summarize", "title", "memory_extract", "judge", "router", "long_form"]


def real_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "llm_mode": "real",
        "openai_api_key": "sk-test-not-a-real-key-0123456789abcdef",
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def primary(model: object) -> ChatOpenAI:
    """The OpenAI client behind whatever wrapper the factory returned."""
    while not isinstance(model, ChatOpenAI):
        model = model.runnable if isinstance(model, RunnableWithFallbacks) else model.bound  # type: ignore[attr-defined]
    return model


def payload(model: ChatOpenAI) -> dict[str, object]:
    return model._get_request_payload([HumanMessage("hi")])


def test_chat_role_uses_responses_api_without_storage() -> None:
    model = primary(get_model("chat", settings=real_settings()))
    assert model.model_name == "gpt-6.1-terra"
    assert model.use_responses_api is True
    sent = payload(model)
    assert sent["store"] is False
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["max_output_tokens"] == 4096
    assert "input" in sent  # Responses API shape, not Chat Completions `messages`
    assert "messages" not in sent


def test_reasoning_roles_request_encrypted_reasoning_so_tool_loops_work_without_storage() -> None:
    sent = payload(primary(get_model("chat", settings=real_settings())))
    assert sent["include"] == ["reasoning.encrypted_content"]


def test_effort_none_roles_do_not_request_reasoning_items() -> None:
    sent = payload(primary(get_model("summarize", settings=real_settings())))
    assert sent["reasoning"] == {"effort": "none"}
    assert "include" not in sent


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "high"])
def test_temperature_is_only_ever_sent_with_effort_none(role: str, effort: str) -> None:
    settings = real_settings(
        llm_chat_reasoning_effort=effort, llm_long_form_reasoning_effort=effort
    )
    sent = payload(primary(get_model(role, settings=settings)))  # type: ignore[arg-type]
    reasoning = sent["reasoning"]
    assert isinstance(reasoning, dict)
    if reasoning["effort"] != "none":
        assert "temperature" not in sent


def test_title_may_use_a_low_temperature_because_its_effort_is_none() -> None:
    sent = payload(primary(get_model("title", settings=real_settings())))
    assert sent["temperature"] == 0.2
    assert sent["max_output_tokens"] == 20


@pytest.mark.parametrize(
    ("role", "model", "effort", "max_out", "timeout", "retries"),
    [
        ("chat", "gpt-6.1-terra", "low", 4096, 60, 2),
        ("summarize", "gpt-6-luna", "none", 800, 30, 2),
        ("title", "gpt-6-luna", "none", 20, 15, 1),
        ("memory_extract", "gpt-6-luna", "none", 600, 30, 2),
        ("judge", "gpt-5.5-2026-04-23", "medium", 2000, 90, 2),
        ("router", "gpt-6-luna", "none", 150, 5, 0),
        ("long_form", "gpt-6.1-terra", "medium", 4000, 120, 2),
    ],
)
def test_role_table_matches_the_design_doc(
    role: str, model: str, effort: str, max_out: int, timeout: float, retries: int
) -> None:
    client = primary(get_model(role, settings=real_settings()))  # type: ignore[arg-type]
    sent = payload(client)
    assert client.model_name == model
    assert sent["reasoning"] == {"effort": effort}
    assert sent["max_output_tokens"] == max_out
    assert client.request_timeout == timeout
    assert client.max_retries == retries


def test_models_and_efforts_come_from_settings() -> None:
    settings = real_settings(
        llm_chat_model="gpt-5.4-mini-2026-03-17", llm_chat_reasoning_effort="none"
    )
    client = primary(get_model("chat", settings=settings))
    assert client.model_name == "gpt-5.4-mini-2026-03-17"
    assert payload(client)["reasoning"] == {"effort": "none"}


def test_chat_has_a_fallback_model_with_effort_none() -> None:
    model = get_model("chat", settings=real_settings())
    assert isinstance(model, RunnableWithFallbacks)
    fallback = primary(model.fallbacks[0])
    assert fallback.model_name == "gpt-6-luna"
    assert payload(fallback)["reasoning"] == {"effort": "none"}


def test_long_form_has_a_fallback_but_summarize_does_not() -> None:
    assert isinstance(get_model("long_form", settings=real_settings()), RunnableWithFallbacks)
    assert isinstance(primary(get_model("summarize", settings=real_settings())), ChatOpenAI)
    assert not isinstance(get_model("summarize", settings=real_settings()), RunnableWithFallbacks)


def test_router_has_no_retries_and_no_fallback() -> None:
    model = get_model("router", settings=real_settings())
    assert not isinstance(model, RunnableWithFallbacks)
    assert primary(model).max_retries == 0


@tool
def lookup(query: str) -> str:
    """Look something up."""
    return query


def test_tools_are_bound_to_both_the_primary_and_the_fallback() -> None:
    model = get_model("chat", tools=[lookup], settings=real_settings())
    assert isinstance(model, RunnableWithFallbacks)
    for runnable in (model.runnable, model.fallbacks[0]):
        assert isinstance(runnable, RunnableBinding)
        assert runnable.kwargs["tools"]


def test_model_override_replaces_the_primary_only() -> None:
    model = get_model("chat", settings=real_settings(), model_override="gpt-5.4-mini-2026-03-17")
    assert isinstance(model, RunnableWithFallbacks)
    assert primary(model).model_name == "gpt-5.4-mini-2026-03-17"
    assert primary(model.fallbacks[0]).model_name == "gpt-6-luna"


def test_missing_api_key_fails_with_a_clear_error() -> None:
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        get_model("chat", settings=real_settings(openai_api_key=""))


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(LLMConfigError, match="role"):
        get_model("wizard", settings=real_settings())  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ROLES)
def test_fake_mode_returns_the_scripted_model_for_every_role(role: str) -> None:
    model = get_model(role, settings=real_settings(llm_mode="fake", openai_api_key=""))  # type: ignore[arg-type]
    assert isinstance(model, FakeChatModel)
    assert model.role == role


def test_fake_mode_needs_no_api_key_and_accepts_tools() -> None:
    settings = real_settings(llm_mode="fake", openai_api_key="")
    assert isinstance(get_model("chat", tools=[lookup], settings=settings), FakeChatModel)


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
def test_a_requested_temperature_is_dropped_whenever_effort_is_not_none(effort: str) -> None:
    from app.llm.factory import _client

    client = _client(
        real_settings(),
        model="gpt-6.1-terra",
        effort=effort,  # type: ignore[arg-type]
        max_output_tokens=100,
        timeout_s=10,
        retries=0,
        temperature=0.2,
    )
    assert "temperature" not in payload(client)


def test_a_requested_temperature_is_kept_at_effort_none() -> None:
    from app.llm.factory import _client

    client = _client(
        real_settings(),
        model="gpt-6-luna",
        effort="none",
        max_output_tokens=100,
        timeout_s=10,
        retries=0,
        temperature=0.2,
    )
    assert payload(client)["temperature"] == 0.2
