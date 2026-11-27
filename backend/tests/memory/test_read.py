"""Choosing which memories go into the prompt (TASK-026, doc 08 section 7 "Read"). Pure: no I/O."""

import uuid
from datetime import UTC, datetime, timedelta

from app.llm.tokens import count_text
from app.memory.read import MAX_MEMORY_TOKENS, select_for_prompt
from app.memory.store import Memory

NOW = datetime(2026, 11, 22, tzinfo=UTC)


def mem(content: str, *, category: str = "fact", age_days: int = 0) -> Memory:
    stamp = NOW - timedelta(days=age_days)
    return Memory(
        id=uuid.uuid4(),
        content=content,
        category=category,
        source="extracted",
        conversation_id=None,
        created_at=stamp,
        updated_at=stamp,
        last_used_at=stamp,
    )


def test_relevant_memories_come_first_then_recent_preferences() -> None:
    relevant = [mem("User works at Acme."), mem("User lives in Pune.")]
    preferences = [mem("User prefers Go.", category="preference")]

    chosen = select_for_prompt(relevant, preferences)

    assert [m.content for m in chosen] == [
        "User works at Acme.",
        "User lives in Pune.",
        "User prefers Go.",
    ]


def test_a_memory_that_is_both_relevant_and_a_recent_preference_appears_once() -> None:
    go = mem("User prefers Go.", category="preference")

    chosen = select_for_prompt([go], [go, mem("User likes tea.", category="preference")])

    assert [m.content for m in chosen] == ["User prefers Go.", "User likes tea."]


def test_nothing_known_gives_nothing() -> None:
    assert select_for_prompt([], []) == []


def test_the_budget_is_600_tokens() -> None:
    assert MAX_MEMORY_TOKENS == 600


def test_stops_at_the_token_budget_and_keeps_the_most_relevant() -> None:
    sentence = "User keeps a very long list of unrelated hobbies and interests. " * 4
    relevant = [mem(f"{i}: {sentence}") for i in range(12)]

    chosen = select_for_prompt(relevant, [], max_tokens=200)

    used = sum(count_text(m.content) for m in chosen)
    assert 0 < len(chosen) < 12
    assert used <= 200
    assert chosen == relevant[: len(chosen)]  # a prefix of the relevance order


def test_a_memory_too_big_for_what_is_left_is_skipped_not_the_rest() -> None:
    small = mem("User prefers Go.")
    big = mem("word " * 300)
    tail = mem("User lives in Pune.")

    chosen = select_for_prompt([small, big, tail], [], max_tokens=60)

    assert [m.content for m in chosen] == ["User prefers Go.", "User lives in Pune."]


def test_at_most_8_relevant_and_5_recent_preferences_are_used() -> None:
    relevant = [mem(f"fact {i}") for i in range(20)]
    preferences = [mem(f"pref {i}", category="preference") for i in range(20)]

    chosen = select_for_prompt(relevant, preferences)

    assert [m.content for m in chosen] == [f"fact {i}" for i in range(8)] + [
        f"pref {i}" for i in range(5)
    ]


def test_only_preferences_count_as_recent_preferences() -> None:
    chosen = select_for_prompt([], [mem("User lives in Pune."), mem("x", category="preference")])

    assert [m.content for m in chosen] == ["x"]
