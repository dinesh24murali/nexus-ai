"""Ingesting the fixture corpus into a throwaway Qdrant collection (fake embeddings)."""

import json
import uuid
from pathlib import Path

import pytest
from app.core.config import Settings
from evals.corpus import ingest_corpus, load_manifest
from qdrant_client import AsyncQdrantClient

from tests.conftest import QDRANT_URL

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, app_env="test", llm_mode="fake", qdrant_url=QDRANT_URL)


async def test_the_corpus_is_searchable_by_its_owner_only(settings: Settings) -> None:
    env = await ingest_corpus(settings)
    try:
        found = await env.retriever.search(env.user_id, "monthly churn decreased onboarding flow")
        assert (found.passages[0].filename, found.passages[0].page) == ("q3-business-review.pdf", 2)

        stranger = await env.retriever.search(
            uuid.uuid4(), "monthly churn decreased onboarding flow"
        )
        assert stranger.passages == []
    finally:
        await env.close()


async def test_recent_files_are_the_five_newest_with_the_battery_brief_first(
    settings: Settings,
) -> None:
    env = await ingest_corpus(settings)
    try:
        listed = await env.recent_files(env.user_id)
        assert len(listed) == 5
        assert listed[0]["filename"] == "research-brief-battery-storage.md"
        assert [f["filename"] for f in listed] == [e.name for e in reversed(load_manifest())][:5]
        assert {f["id"] for f in listed} <= {str(f.id) for f in env.files}
        dates = [f["uploaded_at"] for f in listed]
        assert dates == sorted(dates, reverse=True) and len(set(dates)) == 5
    finally:
        await env.close()


async def test_closing_removes_the_collection(settings: Settings) -> None:
    env = await ingest_corpus(settings)
    collection = env.index.collection
    await env.close()

    client = AsyncQdrantClient(url=QDRANT_URL)
    try:
        assert not await client.collection_exists(collection)
    finally:
        await client.close()


async def test_every_page_of_every_file_is_indexed(settings: Settings) -> None:
    env = await ingest_corpus(settings)
    try:
        total = (await env.index.client.count(env.index.alias, exact=True)).count
        assert total == sum(f.chunks for f in env.files) > 8
        assert env.embedding_tokens > 0
    finally:
        await env.close()


async def test_the_command_ingests_runs_and_cleans_up(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals import run as run_module

    monkeypatch.setattr(run_module, "get_settings", lambda: settings)
    out = tmp_path / "r.json"
    code = await run_module.main_async(
        run_module.parse_args(
            ["--suite", "smoke", "--dataset", "rag.golden", "--out", str(out), "--no-langfuse"]
        )
    )

    assert code == 0
    report = json.loads(out.read_text())
    assert report["run"]["datasets"]["rag.golden"]["cases"] == 10
    assert "rag.context_recall" in report["metrics"]
    client = AsyncQdrantClient(url=QDRANT_URL)
    try:  # the throwaway collection is gone
        names = [c.name for c in (await client.get_collections()).collections]
        assert not [n for n in names if n.startswith("eval_documents_")]
    finally:
        await client.close()


async def test_an_unreachable_vector_store_stops_the_run_with_a_clear_message(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from evals import run as run_module

    down = settings.model_copy(update={"qdrant_url": "http://127.0.0.1:1"})
    monkeypatch.setattr(run_module, "get_settings", lambda: down)
    code = await run_module.main_async(
        run_module.parse_args(["--suite", "smoke", "--out", str(tmp_path / "r.json")])
    )

    assert code == 2
    assert "Qdrant" in capsys.readouterr().err


async def test_another_user_has_no_recent_files(settings: Settings) -> None:
    env = await ingest_corpus(settings)
    try:
        assert await env.recent_files(uuid.uuid4()) == []
    finally:
        await env.close()
