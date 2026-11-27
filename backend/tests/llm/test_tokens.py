import pytest
import tiktoken
from app.llm import tokens
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def test_counts_use_the_o200k_encoding() -> None:
    assert tokens.encoding_available()
    assert tokens.count_text("hello world") == 2
    assert tokens.count_text("The quick brown fox jumps over the lazy dog") == 9


def test_empty_text_is_zero_tokens() -> None:
    assert tokens.count_text("") == 0


def test_message_count_adds_per_message_overhead() -> None:
    one = tokens.count_messages([HumanMessage("hello world")])
    two = tokens.count_messages([HumanMessage("hello world"), HumanMessage("hello world")])
    assert one == 2 + tokens.MESSAGE_OVERHEAD
    assert two == 2 * one


def test_tool_call_arguments_count() -> None:
    plain = tokens.count_messages([AIMessage("")])
    with_call = tokens.count_messages(
        [
            AIMessage(
                "", tool_calls=[{"name": "web_search", "args": {"query": "go release"}, "id": "c1"}]
            )
        ]
    )
    assert with_call > plain


def test_structured_content_blocks_count_their_text_only() -> None:
    message = AIMessage(
        content=[{"type": "text", "text": "hello world"}, {"type": "reasoning", "id": "rs_1"}]
    )
    assert tokens.count_messages([message]) == 2 + tokens.MESSAGE_OVERHEAD


def test_falls_back_to_a_character_estimate_when_the_encoding_cannot_be_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(_: str) -> object:
        raise OSError("no network for the BPE file")

    tokens._encoding.cache_clear()
    monkeypatch.setattr(tiktoken, "get_encoding", offline)
    try:
        assert tokens.encoding_available() is False
        assert tokens.count_text("a" * 400) == 100
        assert tokens.count_text("a") == 1
        assert tokens.count_messages([SystemMessage("a" * 40)]) == 10 + tokens.MESSAGE_OVERHEAD
    finally:
        tokens._encoding.cache_clear()
