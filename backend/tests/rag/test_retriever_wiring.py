"""How production builds the retriever: lazily, through the factory, once."""

import pytest
from app.core.config import Settings
from app.llm.factory import LLMConfigError
from app.rag.retriever import Retriever, lazy_retriever


def settings(**values: object) -> Settings:
    base: dict[str, object] = {
        "llm_mode": "fake",
        "auth_secret": "x" * 32,
        "app_secret": "y" * 32,
    }
    return Settings(**{**base, **values})  # type: ignore[arg-type]


def test_building_it_does_not_touch_the_network_or_the_key() -> None:
    get = lazy_retriever(settings(llm_mode="real", openai_api_key=""))
    assert callable(get)  # nothing was built yet, so no error


def test_the_same_retriever_is_reused() -> None:
    get = lazy_retriever(settings())
    assert isinstance(get(), Retriever)
    assert get() is get()


def test_without_an_api_key_the_first_use_fails_like_the_chat_model_does() -> None:
    get = lazy_retriever(settings(llm_mode="real", openai_api_key=""))
    with pytest.raises(LLMConfigError):
        get()
