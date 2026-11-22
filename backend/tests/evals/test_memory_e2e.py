"""The `memory.e2e` step (TASK-039) against a real Postgres store, the real graph and scripted
models: the committed cases can pass, a graph that deletes the wrong memory fails them, and
nothing is left behind. (A real model run belongs to `make eval`.)"""

import re
import uuid
from collections.abc import Sequence
from typing import Any

import psycopg
import pytest
from app.core.config import Settings
from app.graph.builder import build_graph
from app.graph.state import ChatState
from app.llm.fake import FakeChatModel, FakeEmbeddingsClient, _Plan
from app.memory.manage import StoreMemoryManager
from app.memory.read import read_for_prompt
from app.rag.embedder import Embedder
from evals.datasets import DATASETS_DIR, Dataset, MemoryE2ECase, load_dataset
from evals.langfuse_log import EvalLogger
from evals.memory_e2e import E2EEnv, check_cards, check_store, run_memory_e2e
from evals.run import SuiteRun
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

pytestmark = pytest.mark.integration

NATURAL = re.compile(r"^remember that (.+?)\.?$", re.IGNORECASE)


class Router(FakeChatModel):
    """The fake router plus one plain-text rule: "Remember that X" saves X (what the real router
    is asked to do)."""

    @staticmethod
    def _route(prompt: str) -> str:
        newest = re.findall(r"<message>(.*?)</message>", prompt, re.S)[-1]
        if match := NATURAL.match(newest.strip()):
            slots = dict.fromkeys(("topic", "source", "output", "memory_action", "memory_content"))
            slots.update(memory_action="save", memory_content=match.group(1))
            import json

            return json.dumps({"intent": "manage_memory", "confidence": 0.95, "slots": slots})
        return FakeChatModel._route(prompt)


class MemoryEcho(FakeChatModel):
    """A chat model that answers with the `<user_memory>` block it was shown (so a test can see
    that conversation B had the memory)."""

    def _plan(self, messages: Sequence[BaseMessage]) -> _Plan:
        shown = "\n".join(
            m.text for m in messages if isinstance(m, HumanMessage) and "<user_memory>" in m.text
        )
        return self._plain(shown or "no memory shown", 10)


def make_suite() -> SuiteRun:
    settings = Settings(_env_file=None, app_env="test", llm_mode="fake")
    return SuiteRun(
        settings=settings,
        graph=None,  # type: ignore[arg-type]
        summarizer=None,
        judges=None,
        logger=EvalLogger(None, run_name="t", environment="test"),
        runs=1,
    )


@pytest.fixture
def env(db_url: str) -> E2EEnv:
    embedder = Embedder(FakeEmbeddingsClient(), "text-embedding-3-small")

    async def enabled(user_id: Any) -> bool:
        return True

    manager = StoreMemoryManager(db_url, lambda: embedder, enabled)

    async def lookup(user_id: Any, query: str) -> Any:
        return await read_for_prompt(db_url, embedder, user_id, query)

    def graph_for(user: uuid.UUID) -> Any:
        return build_graph(
            checkpointer=InMemorySaver(),
            chat_model=MemoryEcho(role="chat"),
            summarize_model=FakeChatModel(role="summarize"),
            router_model=Router(role="router"),
            memory_manager=manager,
            memories=lookup,
        )

    return E2EEnv(db_url, embedder, graph_for)


async def run_cases(env: E2EEnv, cases: list[MemoryE2ECase]) -> SuiteRun:
    suite = make_suite()
    await run_memory_e2e(suite, Dataset("memory.e2e", "v", cases), env=env)
    return suite


def committed() -> list[MemoryE2ECase]:
    return load_dataset("memory.e2e", MemoryE2ECase).cases


def problems_of(suite: SuiteRun) -> dict[str, list[str]]:
    return {r.case_id: r.notes["problems"] if not r.error else [r.error] for r in suite.records}


# -- the dataset -------------------------------------------------------------------------------


def test_the_dataset_is_the_planned_size_and_covers_the_documented_cases() -> None:
    cases = {c.id: c for c in committed()}
    assert len(cases) == 8
    assert len(load_dataset("memory.e2e", MemoryE2ECase, smoke_only=True).cases) == 2
    # GAP-07 (duplicates), GAP-08 (one clear match, ambiguous), reject, list, saved in A used in B
    assert cases["e2e-03"].turns_a[0] == cases["e2e-03"].turns_a[1]
    assert cases["e2e-05"].expect_candidates == ["Acme"] and cases["e2e-05"].decide == "approve"
    assert len(cases["e2e-06"].expect_candidates or []) == 2 and cases["e2e-06"].pick == 1
    assert cases["e2e-07"].decide == "reject"
    assert cases["e2e-01"].probe_b and cases["e2e-01"].expect_in_answer == ["Go"]


def test_the_record_model_rejects_inconsistent_cases() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MemoryE2ECase(id="x-1", turns_a=["/memory"], expect_in_answer=["x"])  # no probe
    with pytest.raises(ValidationError):
        MemoryE2ECase(id="x-1", turns_a=["/memory"], expect_candidates=["x"])  # no decision
    with pytest.raises(ValidationError):
        MemoryE2ECase(
            id="x-1", turns_a=["/memory"], expect_candidates=["x"], decide="approve", pick=3
        )


def test_the_check_helpers_name_the_problem_not_the_text() -> None:
    case = MemoryE2ECase(
        id="x-1",
        turns_a=["/memory"],
        expect_memories=["Go"],
        expect_absent=["Acme"],
        expect_count=1,
        expect_candidates=["a", "b"],
        decide="approve",
    )
    assert check_store(case, ["User likes Go", "User works at Acme"]) == [
        "memory_that_should_be_gone_remains",
        "wrong_memory_count",
    ]
    assert check_store(case, ["User likes Go and Go"]) == []
    assert check_cards(case, []) == ["no_card"]
    assert check_cards(case, [[{"memory_id": "1", "content": "A thing"}]]) == ["wrong_candidates"]
    assert (
        check_cards(
            case,
            [[{"memory_id": "1", "content": "b"}, {"memory_id": "2", "content": "a"}]],
        )
        == []
    )


# -- the step ----------------------------------------------------------------------------------


async def test_every_committed_case_passes_through_manage_memory(env: E2EEnv) -> None:
    suite = await run_cases(env, committed())

    assert problems_of(suite) == {f"e2e-0{i}": [] for i in range(1, 9)}
    assert suite.scores.mean("memory.e2e_recall") == 1.0
    assert suite.errors == 0


async def test_conversation_b_sees_what_conversation_a_saved(env: E2EEnv) -> None:
    suite = await run_cases(env, [c for c in committed() if c.id == "e2e-01"])

    assert "User prefers Go for backend work." in suite.records[0].output


async def test_the_step_leaves_no_memories_behind(env: E2EEnv, db: psycopg.Connection[Any]) -> None:
    await run_cases(env, committed())

    assert db.execute("SELECT count(*) FROM store WHERE prefix LIKE 'memories.%'").fetchone() == (
        0,
    )


async def test_a_case_that_does_not_hold_scores_zero_and_names_why(env: E2EEnv) -> None:
    broken = MemoryE2ECase(
        id="x-1",
        turns_a=["/memory remember I prefer Go."],
        expect_count=2,
        expect_in_replies=["something nobody says"],
    )

    suite = await run_cases(env, [broken])

    assert suite.scores.mean("memory.e2e_recall") == 0.0
    assert problems_of(suite) == {"x-1": ["reply_missing", "wrong_memory_count"]}


async def test_a_graph_that_deletes_the_wrong_memory_fails_the_forget_cases(
    env: E2EEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step can fail: with the choice check replaced by "the first candidate", a reject and
    a pick of the second candidate are not honoured."""
    import app.graph.intents.manage_memory as manage_memory

    monkeypatch.setattr(manage_memory, "chosen_memory", lambda answer, candidates: candidates[0])

    suite = await run_cases(env, [c for c in committed() if c.id in ("e2e-06", "e2e-07")])

    found = problems_of(suite)
    assert "wrong_number_deleted" in found["e2e-07"]  # a rejected card still deleted
    assert "picked_memory_still_there" in found["e2e-06"] or "wrong_candidates" in found["e2e-06"]
    assert suite.scores.mean("memory.e2e_recall") == 0.0


async def test_a_card_that_comes_after_the_deletion_is_caught(
    env: E2EEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the memory were deleted first and asked about afterwards, the count check shows it."""
    import app.graph.intents.manage_memory as manage_memory

    original = manage_memory.make_manage_memory_node

    def deleting_first(manager: Any) -> Any:
        node = original(manager)

        async def run(state: ChatState, config: RunnableConfig) -> Any:
            out = await node(state, config)
            user = uuid.UUID(config["configurable"]["user_id"])
            for candidate in out.get("forget_candidates") or []:
                await manager.forget(user, uuid.UUID(candidate["memory_id"]))
            return out

        return run

    monkeypatch.setattr("app.graph.builder.make_manage_memory_node", deleting_first)

    suite = await run_cases(env, [c for c in committed() if c.id == "e2e-05"])

    assert "deleted_before_the_card" in problems_of(suite)["e2e-05"]


async def test_an_infrastructure_error_is_a_miss_not_a_crash(env: E2EEnv) -> None:
    def broken(user: uuid.UUID) -> Any:
        raise RuntimeError("no graph")

    suite = await run_cases(E2EEnv(env.database_url, env.embedder, broken), [committed()[0]])

    assert suite.scores.mean("memory.e2e_recall") == 0.0
    assert suite.errors == 1 and suite.records[0].error == "RuntimeError"


def test_no_dataset_record_holds_a_secret() -> None:
    text = (DATASETS_DIR / "memory.e2e.jsonl").read_text()
    assert "@" not in text and "sk-" not in text
