from dataclasses import dataclass, field
from typing import Any

import pytest
from app.llm.prompt_sync import PromptSyncError, SyncAction, sync_prompts
from app.llm.prompts import Prompt


class NotFound(Exception):
    status_code = 404


@dataclass
class Remote:
    prompt: str
    config: dict[str, Any]


@dataclass
class StubLangfuse:
    """Just enough of the Langfuse client: production-labelled text prompts keyed by name."""

    production: dict[str, Remote] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)
    fail_get_with: Exception | None = None

    def get_prompt(self, name: str, *, label: str, type: str, **_: Any) -> Remote:
        assert label == "production"
        assert type == "text"
        if self.fail_get_with:
            raise self.fail_get_with
        if name not in self.production:
            raise NotFound(name)
        return self.production[name]

    def create_prompt(self, **kwargs: Any) -> None:
        self.created.append(kwargs)
        self.production[kwargs["name"]] = Remote(kwargs["prompt"], kwargs["config"])


def local(name: str = "nexus.system", version: int = 1, body: str = "Hello") -> Prompt:
    return Prompt(name=name, version=version, body=body, meta={})


def test_new_prompt_is_created_with_the_production_label() -> None:
    client = StubLangfuse()
    results = sync_prompts(client, [local()])
    assert [(r.name, r.action) for r in results] == [("nexus.system", SyncAction.CREATED)]
    created = client.created[0]
    assert created["labels"] == ["production"]
    assert created["type"] == "text"
    assert created["prompt"] == "Hello"
    assert created["config"] == {"repo_version": 1}


def test_unchanged_prompt_is_left_alone() -> None:
    client = StubLangfuse({"nexus.system": Remote("Hello", {"repo_version": 1})})
    results = sync_prompts(client, [local()])
    assert results[0].action is SyncAction.UNCHANGED
    assert client.created == []


def test_bumped_version_with_new_text_is_pushed() -> None:
    client = StubLangfuse({"nexus.system": Remote("Hello", {"repo_version": 1})})
    results = sync_prompts(client, [local(version=2, body="Hello again")])
    assert results[0].action is SyncAction.UPDATED
    assert client.created[0]["config"] == {"repo_version": 2}
    assert "2" in client.created[0]["commit_message"]


def test_changed_text_without_a_version_bump_is_an_error() -> None:
    client = StubLangfuse({"nexus.system": Remote("Hello", {"repo_version": 1})})
    with pytest.raises(PromptSyncError, match="bump"):
        sync_prompts(client, [local(version=1, body="Changed without bumping")])
    assert client.created == []


def test_a_lower_repo_version_than_langfuse_is_an_error() -> None:
    client = StubLangfuse({"nexus.system": Remote("Hello", {"repo_version": 5})})
    with pytest.raises(PromptSyncError, match="older"):
        sync_prompts(client, [local(version=4, body="Other")])


def test_dry_run_reports_without_writing() -> None:
    client = StubLangfuse()
    results = sync_prompts(client, [local()], dry_run=True)
    assert results[0].action is SyncAction.CREATED
    assert client.created == []


def test_only_changed_prompts_are_written() -> None:
    client = StubLangfuse({"a.one": Remote("A", {"repo_version": 1})})
    results = sync_prompts(client, [local("a.one", 1, "A"), local("b.two", 1, "B")])
    assert {r.name: r.action for r in results} == {
        "a.one": SyncAction.UNCHANGED,
        "b.two": SyncAction.CREATED,
    }
    assert [c["name"] for c in client.created] == ["b.two"]


def test_other_langfuse_errors_are_not_mistaken_for_missing_prompts() -> None:
    client = StubLangfuse(fail_get_with=RuntimeError("network down"))
    with pytest.raises(RuntimeError, match="network down"):
        sync_prompts(client, [local()])
    assert client.created == []
