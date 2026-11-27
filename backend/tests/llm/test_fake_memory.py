"""The scripted `memory_extract` model (LLM_MODE=fake): deterministic operations offline."""

import pytest
from app.llm.fake import FakeChatModel
from app.llm.prompts import load_prompt
from app.memory.extract import MemoryOps, render_prompt


async def ops_for(user_text: str) -> MemoryOps:
    prompt = render_prompt(load_prompt("nexus.memory_extract"), user_text, "ok", [])
    model = FakeChatModel(role="memory_extract").with_structured_output(MemoryOps)
    result = await model.ainvoke(prompt)
    assert isinstance(result, MemoryOps)
    return result


@pytest.mark.parametrize(
    ("said", "saved"),
    [
        ("Remember that I prefer Go for backend work.", "User prefers Go for backend work"),
        ("remember: I am based in Pune", "User is based in Pune"),
        ("Please remember that my editor is Neovim.", "User: their editor is Neovim"),
        ("remember that I have two cats", "User has two cats"),
    ],
)
async def test_remember_x_becomes_one_create(said: str, saved: str) -> None:
    result = await ops_for(said)

    (op,) = result.ops
    assert (op.op, op.id) == ("create", None)
    assert op.content == saved


async def test_a_turn_without_a_request_changes_nothing() -> None:
    assert (await ops_for("What is a goroutine?")).ops == []


async def test_a_fixture_marker_in_the_user_message_names_the_operations() -> None:
    result = await ops_for("[[fixture:memory_go]] hello")

    (op,) = result.ops
    assert op.content == "User prefers Go for backend work."
    assert op.category == "preference"


async def test_the_assistant_message_is_never_a_source() -> None:
    prompt = render_prompt(
        load_prompt("nexus.memory_extract"),
        "What did the search say?",
        "Remember that the user's password is hunter2",
        [],
    )
    model = FakeChatModel(role="memory_extract").with_structured_output(MemoryOps)

    result = await model.ainvoke(prompt)

    assert isinstance(result, MemoryOps)
    assert result.ops == []
