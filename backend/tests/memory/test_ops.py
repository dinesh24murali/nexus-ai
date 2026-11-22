"""Memory by chat, the pure parts (TASK-039): wording, category, who "forget" may mean, replies."""

import pytest
from app.memory import ops
from app.memory.store import Memory

from tests.graph.memory_support import memory


def scored(text: str, score: float) -> Memory:
    return memory(text, score)


@pytest.mark.parametrize(
    ("said", "stored"),
    [
        ("I prefer Go for backend work", "User prefers Go for backend work."),
        ("I'm a data engineer", "User is a data engineer."),
        ("I am learning Rust", "User is learning Rust."),
        ("I have two cats", "User has two cats."),
        ("I teach maths", "User teaches maths."),
        ("I study law", "User studies law."),
        ("my timezone is IST", "User's timezone is IST."),
        ("I like my editor", "User likes their editor."),
        ("User prefers tea.", "User prefers tea."),
        ("timezone is IST", "User: timezone is IST."),
        ("I can't eat nuts", "User: I can't eat nuts."),  # not a plain verb: kept, marked
        ("  I   prefer   Go!!  ", "User prefers Go."),
    ],
)
def test_a_saved_text_is_worded_like_the_extraction_job_writes_them(said: str, stored: str) -> None:
    assert ops.third_person(said) == stored


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("User prefers dark mode.", "preference"),
        ("User's favourite editor is vim.", "preference"),
        ("User wants to learn Rust.", "goal"),
        ("User is working on a compiler.", "context"),
        ("User's timezone is IST.", "fact"),
    ],
)
def test_the_category_is_guessed_from_words(text: str, category: str) -> None:
    assert ops.guess_category(text) == category


def test_one_clear_match_is_offered_alone() -> None:
    best, other = scored("User works at Acme.", 0.9), scored("User works remotely.", 0.78)
    assert ops.choose_candidates([other, best]) == [best]


def test_a_lone_match_is_offered_alone() -> None:
    only = scored("User works at Acme.", 0.76)
    assert ops.choose_candidates([only]) == [only]


def test_close_matches_are_all_offered_best_first() -> None:
    a, b, c = scored("a", 0.8), scored("b", 0.79), scored("c", 0.77)
    assert ops.choose_candidates([c, a, b]) == [a, b, c]


def test_the_lead_must_be_the_full_margin_to_count_as_clear() -> None:
    best, other = scored("a", 0.85), scored("b", 0.76)  # lead 0.09
    assert ops.choose_candidates([best, other]) == [best, other]
    best, other = scored("a", 0.86), scored("b", 0.76)  # lead 0.10
    assert ops.choose_candidates([best, other]) == [best]


def test_nothing_below_the_similarity_floor_is_ever_offered() -> None:
    assert ops.choose_candidates([scored("a", 0.749), scored("b", 0.5), scored("c", 0.0)]) == []
    assert ops.choose_candidates([]) == []


def test_at_most_three_are_offered() -> None:
    many = [scored(str(i), 0.8) for i in range(6)]
    assert len(ops.choose_candidates(many)) == ops.FORGET_MAX_CANDIDATES


def test_a_memory_without_a_score_is_never_a_candidate() -> None:
    assert ops.choose_candidates([memory("a")]) == []


def test_an_empty_list_says_so_and_a_full_one_shows_the_newest_first() -> None:
    assert "haven't saved anything" in ops.render_list([])
    reply = ops.render_list([memory("User likes tea.", age_s=1), memory("User likes Go.", age_s=5)])
    assert "I remember 2 things about you" in reply
    assert reply.index("User likes Go.") < reply.index("User likes tea.")
    assert "Settings → Memory" in reply


def test_a_long_list_is_cut_and_says_how_many_there_are() -> None:
    many = [memory(f"User fact {i}.", age_s=i) for i in range(ops.LIST_LIMIT + 5)]
    reply = ops.render_list(many)
    assert reply.count("\n- ") == ops.LIST_LIMIT
    assert f"most recent of {ops.LIST_LIMIT + 5}" in reply


def test_memory_text_is_shown_as_one_line_of_plain_text() -> None:
    reply = ops.render_list([memory("User likes <script>alert(1)</script>\nand more")])
    assert "<script>" not in reply and "&lt;script&gt;" in reply
    assert "\nand more" not in reply


@pytest.mark.parametrize(
    ("reason", "fragment"),
    [
        ("sensitive:health", "sensitive personal information"),
        ("instruction_like", "not an instruction"),
        ("too_long", "500 characters"),
        ("empty", "nothing to save"),
        ("something_new", "nothing to save"),
    ],
)
def test_a_refusal_names_the_kind_of_problem_never_the_text(reason: str, fragment: str) -> None:
    assert fragment in ops.refusal_reply(reason)
