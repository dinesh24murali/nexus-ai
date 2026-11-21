"""Writing a plan: near-duplicates are dropped, missing targets are skipped, the cap holds
(TASK-025). Real Postgres."""

import uuid

import pytest
from app.memory.extract import Plan, PlannedOp, apply_plan
from app.memory.store import MemoryStore

from tests.memory.conftest import remember, users

pytestmark = pytest.mark.integration


def create(content: str, category: str = "fact") -> PlannedOp:
    return PlannedOp("create", None, content, category)


async def test_a_create_is_written_with_its_source_and_conversation(store: MemoryStore) -> None:
    (alice,) = users(1)
    conversation = uuid.uuid4()

    applied = await apply_plan(
        store, alice, conversation, Plan([create("User prefers Go.", "preference")])
    )

    assert (applied.created, applied.duplicates) == (1, 0)
    (memory,) = await store.all(alice)
    assert (memory.content, memory.category, memory.source) == (
        "User prefers Go.",
        "preference",
        "extracted",
    )
    assert memory.conversation_id == conversation


async def test_a_create_that_repeats_an_existing_memory_is_dropped(store: MemoryStore) -> None:
    (alice,) = users(1)
    await remember(store, alice, "User prefers Go for backend work.")

    applied = await apply_plan(
        store, alice, None, Plan([create("User prefers Go for backend work.")])
    )

    assert (applied.created, applied.duplicates) == (0, 1)
    assert await store.count(alice) == 1


async def test_a_different_memory_is_not_mistaken_for_a_duplicate(store: MemoryStore) -> None:
    (alice,) = users(1)
    await remember(store, alice, "User prefers Go for backend work.")

    applied = await apply_plan(store, alice, None, Plan([create("User lives in Pune.")]))

    assert applied.created == 1
    assert await store.count(alice) == 2


async def test_two_creates_of_the_same_thing_in_one_turn_make_one_memory(
    store: MemoryStore,
) -> None:
    (alice,) = users(1)

    applied = await apply_plan(
        store, alice, None, Plan([create("User likes tea."), create("User likes tea.")])
    )

    assert (applied.created, applied.duplicates) == (1, 1)


async def test_another_users_identical_memory_is_not_a_duplicate(store: MemoryStore) -> None:
    alice, bob = users()
    await remember(store, bob, "User likes tea.")

    applied = await apply_plan(store, alice, None, Plan([create("User likes tea.")]))

    assert applied.created == 1  # duplicates are judged inside the user's own namespace


async def test_updates_and_deletes_change_the_named_memory(store: MemoryStore) -> None:
    (alice,) = users(1)
    keep = await remember(store, alice, "User writes Python.")
    drop = await remember(store, alice, "User lives in Pune.")
    plan = Plan(
        [
            PlannedOp("update", keep, "User writes Rust.", "fact"),
            PlannedOp("delete", drop, None, None),
        ]
    )

    applied = await apply_plan(store, alice, None, plan)

    assert (applied.updated, applied.deleted, applied.missing) == (1, 1, 0)
    assert [m.content for m in await store.all(alice)] == ["User writes Rust."]


async def test_an_update_or_delete_of_a_memory_that_is_gone_is_skipped(
    store: MemoryStore,
) -> None:
    (alice,) = users(1)
    gone = uuid.uuid4()

    applied = await apply_plan(
        store,
        alice,
        None,
        Plan([PlannedOp("update", gone, "x", None), PlannedOp("delete", gone, None, None)]),
    )

    assert (applied.updated, applied.deleted, applied.missing) == (0, 0, 2)
    assert await store.count(alice) == 0


async def test_a_plan_cannot_reach_another_users_memory_even_with_its_id(
    store: MemoryStore,
) -> None:
    alice, bob = users()
    target = await remember(store, bob, "User likes tea.")

    applied = await apply_plan(
        store,
        alice,
        None,
        Plan(
            [
                PlannedOp("update", target, "Overwritten", None),
                PlannedOp("delete", target, None, None),
            ]
        ),
    )

    assert applied.missing == 2
    kept = await store.get(bob, target)
    assert kept is not None and kept.content == "User likes tea."


async def test_creating_past_the_cap_evicts_the_least_recently_used(store: MemoryStore) -> None:
    (alice,) = users(1)
    oldest = await remember(store, alice, "User fact alpha.")
    await remember(store, alice, "User fact beta.")
    await remember(store, alice, "User fact gamma.")

    applied = await apply_plan(store, alice, None, Plan([create("User fact delta.")]), cap=3)

    assert (applied.created, applied.evicted) == (1, 1)
    assert await store.count(alice) == 3
    assert await store.get(alice, oldest) is None
