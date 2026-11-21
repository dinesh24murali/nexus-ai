"""Dataset loading: JSONL files in `evals/datasets/`, validated record by record.

A bad record fails the whole run with its file and line: a silently skipped record would shrink a
metric's denominator and make a gate easier to pass. Ids are stable and unique per file. The
dataset version is a hash of the file bytes, so a run says exactly which records it used.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASETS_DIR = REPO_ROOT / "evals" / "datasets"


class DatasetError(Exception):
    pass


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)
    smoke: bool = False


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)


class ChatCase(_Record):
    input: str = Field(min_length=1)
    expect: Expect = Field(default_factory=Expect)
    should_refuse: bool = False


class SummaryCase(_Record):
    messages: list[Turn] = Field(min_length=2)
    key_facts: list[str] = Field(min_length=1)


class PerfCase(_Record):
    input: str = Field(min_length=1)
    history: list[Turn] = Field(default_factory=list)
    uses_tools: bool = False


class Canaries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt_leak: bool = False  # the system prompt must not appear in the output
    must_not_appear: list[str] = Field(default_factory=list)
    must_not_match: list[str] = Field(default_factory=list)  # regular expressions
    max_output_chars: int | None = Field(default=None, gt=0)


class RedTeamCase(_Record):
    threat_id: str = Field(pattern=r"^LLM-T\d{2}$")
    input: str = Field(min_length=1)
    should_refuse: bool = False
    canaries: Canaries = Field(default_factory=Canaries)


# dataset name -> record model. A dataset that is not listed here has no suite yet.
MODELS: dict[str, type[_Record]] = {
    "chat.golden": ChatCase,
    "summary.golden": SummaryCase,
    "perf.latency": PerfCase,
    "safety.redteam": RedTeamCase,
}


@dataclass(frozen=True)
class Dataset[T: _Record]:
    name: str
    version: str
    cases: list[T]


def load_dataset[T: _Record](
    name: str, model: type[T], *, smoke_only: bool = False, directory: Path | None = None
) -> Dataset[T]:
    path = (directory or DATASETS_DIR) / f"{name}.jsonl"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise DatasetError(f"dataset not found: {path}") from None

    cases: list[T] = []
    seen: set[str] = set()
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            case = model.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetError(f"{where}: invalid record ({type(exc).__name__}): {exc}") from None
        if case.id in seen:
            raise DatasetError(f"{where}: duplicate id {case.id!r}")
        seen.add(case.id)
        if case.smoke or not smoke_only:
            cases.append(case)
    if not cases:
        raise DatasetError(f"{path.name}: no records selected")
    return Dataset(name=name, version=hashlib.sha256(raw).hexdigest()[:12], cases=cases)
