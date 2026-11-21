from pathlib import Path

import pytest
from app.llm.prompts import PROMPTS_DIR, Prompt, PromptError, all_prompts, load_prompt


def write(directory: Path, name: str, text: str) -> None:
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


def test_loads_front_matter_and_body(tmp_path: Path) -> None:
    write(
        tmp_path,
        "demo.hello",
        "---\nname: demo.hello\nversion: 3\nmin_confidence: 0.7\n---\nHi there\n",
    )
    prompt = load_prompt("demo.hello", directory=tmp_path)
    assert prompt.name == "demo.hello"
    assert prompt.version == 3
    assert prompt.meta["min_confidence"] == 0.7
    assert prompt.body == "Hi there"


def test_render_substitutes_variables(tmp_path: Path) -> None:
    write(
        tmp_path,
        "demo.ctx",
        "---\nname: demo.ctx\nversion: 1\n---\nToday is {{today}} for {{ user }}.\n",
    )
    prompt = load_prompt("demo.ctx", directory=tmp_path)
    assert prompt.variables == frozenset({"today", "user"})
    assert prompt.render(today="2026-11-21", user="Ada") == "Today is 2026-11-21 for Ada."


def test_render_does_not_reinterpret_substituted_values(tmp_path: Path) -> None:
    write(tmp_path, "demo.ctx", "---\nname: demo.ctx\nversion: 1\n---\nA={{a}} B={{b}}\n")
    prompt = load_prompt("demo.ctx", directory=tmp_path)
    assert prompt.render(a="{{b}}", b="x") == "A={{b}} B=x"


def test_render_rejects_missing_and_unexpected_variables(tmp_path: Path) -> None:
    write(tmp_path, "demo.ctx", "---\nname: demo.ctx\nversion: 1\n---\n{{a}}\n")
    prompt = load_prompt("demo.ctx", directory=tmp_path)
    with pytest.raises(PromptError, match="missing"):
        prompt.render()
    with pytest.raises(PromptError, match="unexpected"):
        prompt.render(a="1", b="2")


def test_prompt_without_variables_renders_to_the_same_text_every_time(tmp_path: Path) -> None:
    write(tmp_path, "demo.fixed", "---\nname: demo.fixed\nversion: 1\n---\nFixed text.\n")
    prompt = load_prompt("demo.fixed", directory=tmp_path)
    assert prompt.render() == prompt.render() == "Fixed text."


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("no front matter", "front-matter"),
        ("---\nname: demo.bad\n---\nbody", "version"),
        ("---\nname: demo.bad\nversion: 0\n---\nbody", "version"),
        ("---\nname: demo.bad\nversion: one\n---\nbody", "version"),
        ("---\nname: other.name\nversion: 1\n---\nbody", "name"),
        ("---\nname: demo.bad\nversion: 1\n---\n\n", "empty"),
        ("---\n: : :\n---\nbody", "front-matter"),
    ],
)
def test_invalid_prompt_files_are_rejected(tmp_path: Path, text: str, match: str) -> None:
    write(tmp_path, "demo.bad", text)
    with pytest.raises(PromptError, match=match):
        load_prompt("demo.bad", directory=tmp_path)


def test_unknown_prompt_raises(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="not found"):
        load_prompt("nope.nothing", directory=tmp_path)


@pytest.mark.parametrize("name", ["../etc/passwd", "a/b", "A.B", "", "x" * 100])
def test_prompt_names_cannot_escape_the_directory(tmp_path: Path, name: str) -> None:
    with pytest.raises(PromptError, match="invalid prompt name"):
        load_prompt(name, directory=tmp_path)


def test_all_prompts_lists_every_file_sorted(tmp_path: Path) -> None:
    write(tmp_path, "b.two", "---\nname: b.two\nversion: 1\n---\nb\n")
    write(tmp_path, "a.one", "---\nname: a.one\nversion: 2\n---\na\n")
    assert [p.name for p in all_prompts(tmp_path)] == ["a.one", "b.two"]


# --- the real prompts shipped in backend/prompts ---


def test_every_shipped_prompt_is_valid() -> None:
    prompts = all_prompts(PROMPTS_DIR)
    assert prompts, "backend/prompts must not be empty"
    assert all(isinstance(p, Prompt) and p.version >= 1 for p in prompts)


def test_system_prompt_is_fixed_text_for_caching() -> None:
    assert load_prompt("nexus.system").variables == frozenset()


def test_context_prompt_variables() -> None:
    assert load_prompt("nexus.context").variables == frozenset(
        {"user_display_name", "today", "memories_block", "recent_files_block"}
    )


def test_summarize_prompt_variables() -> None:
    assert load_prompt("nexus.summarize").variables == frozenset({"existing_summary", "messages"})
