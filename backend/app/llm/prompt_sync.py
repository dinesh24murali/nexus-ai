"""Mirror repo prompts to Langfuse (ADR-0008). The repo is canonical; Langfuse is a copy used for
trace linking and version history, never read at runtime.

A prompt's Langfuse `production` version carries `config.repo_version`. Rules:
- missing in Langfuse                      -> create
- same text                                -> unchanged
- text differs, repo version is higher     -> create a new Langfuse version
- text differs, repo version is not higher -> error: the prompt changed without a version bump
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.llm.prompts import Prompt


class PromptSyncError(Exception):
    pass


class SyncAction(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class SyncResult:
    name: str
    version: int
    action: SyncAction


def _remote(client: Any, name: str) -> Any | None:
    try:
        return client.get_prompt(name, label="production", type="text")
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return None
        raise


def sync_prompts(
    client: Any, prompts: Iterable[Prompt], *, dry_run: bool = False
) -> list[SyncResult]:
    plan: list[tuple[Prompt, SyncAction]] = []
    for prompt in prompts:
        remote = _remote(client, prompt.name)
        if remote is None:
            plan.append((prompt, SyncAction.CREATED))
        elif remote.prompt == prompt.body:
            plan.append((prompt, SyncAction.UNCHANGED))
        else:
            remote_version = int((remote.config or {}).get("repo_version", 0))
            if prompt.version < remote_version:
                raise PromptSyncError(
                    f"{prompt.name}: repo version {prompt.version} is older than "
                    f"Langfuse's {remote_version}"
                )
            if prompt.version == remote_version:
                raise PromptSyncError(
                    f"{prompt.name}: text changed but version is still {prompt.version}; "
                    "bump `version` in the front-matter (ADR-0008)"
                )
            plan.append((prompt, SyncAction.UPDATED))

    # Validate everything before writing anything, so a bad prompt never leaves a partial sync.
    if not dry_run:
        for prompt, action in plan:
            if action is not SyncAction.UNCHANGED:
                client.create_prompt(
                    name=prompt.name,
                    prompt=prompt.body,
                    labels=["production"],
                    type="text",
                    config={"repo_version": prompt.version},
                    commit_message=f"repo version {prompt.version}",
                )
    return [SyncResult(p.name, p.version, action) for p, action in plan]
