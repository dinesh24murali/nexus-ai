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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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


class ChunkRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)  # null for formats without pages


RagKind = Literal["fact", "multi", "whole_doc", "unanswerable"]


class RagCase(_Record):
    """A question over the fixture corpus (`evals/fixtures/docs/`). Doc 09 fields plus `kind`
    (what the case probes), `evidence` (phrases that must be on the referenced pages: a test checks
    them against the real parser, so a wrong reference cannot blame the model) and `key_facts`
    (what a correct answer must contain; defaults to the reference answer as one fact)."""

    input: str = Field(min_length=1)
    kind: RagKind
    reference_answer: str = Field(min_length=1)
    reference_chunks: list[ChunkRef] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    answerable: bool

    @model_validator(mode="after")
    def _consistent(self) -> RagCase:
        if self.answerable != (self.kind != "unanswerable"):
            raise ValueError("`answerable` must be false exactly for kind `unanswerable`")
        if self.answerable and not self.reference_chunks:
            raise ValueError("an answerable case needs reference_chunks")
        if not self.answerable and (self.reference_chunks or self.evidence):
            raise ValueError("an unanswerable case has no reference_chunks or evidence")
        return self


ToolName = Literal["search_my_files", "web_search", "calculator"]


class SetupDoc(BaseModel):
    """A file the case's user has uploaded: the search tool finds exactly this text."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ToolsSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docs: list[SetupDoc] = Field(default_factory=list)


class ToolsCase(_Record):
    """A tool-routing case (doc 09 sections 3 and 9). `expected_tool` is the FIRST tool call
    (null: no tool at all); `expected_args_contains` maps an argument of that call to substrings
    it must contain (case-insensitive); `expected_sequence` lists every call in order once every
    approval is granted; `expect_interrupt` is true exactly when a web search is expected."""

    input: str = Field(min_length=1)
    setup: ToolsSetup = Field(default_factory=ToolsSetup)
    expected_tool: ToolName | None
    expected_args_contains: dict[str, list[str]] = Field(default_factory=dict)
    expect_interrupt: bool
    expected_sequence: list[ToolName] | None = Field(default=None, min_length=2)

    @model_validator(mode="after")
    def _consistent(self) -> ToolsCase:
        wanted = self.expected_sequence or ([self.expected_tool] if self.expected_tool else [])
        if self.expect_interrupt != ("web_search" in wanted):
            raise ValueError("`expect_interrupt` must be true exactly when web_search is expected")
        if self.expected_args_contains and self.expected_tool is None:
            raise ValueError("`expected_args_contains` needs an expected_tool")
        if self.expected_sequence and self.expected_sequence[0] != self.expected_tool:
            raise ValueError("`expected_sequence` must start with the expected_tool")
        return self


class Canaries(BaseModel):
    """What must never happen. Text checks look at the answer; tool checks look at the calls the
    model made (`forbidden_tool_calls` even if the approval card would have caught them)."""

    model_config = ConfigDict(extra="forbid")

    system_prompt_leak: bool = False  # the system prompt must not appear in the output
    must_not_appear: list[str] = Field(default_factory=list)
    must_not_match: list[str] = Field(default_factory=list)  # regular expressions
    max_output_chars: int | None = Field(default=None, gt=0)
    forbidden_tool_calls: list[ToolName] = Field(default_factory=list)  # may not even be requested
    require_approval: list[Literal["web_search"]] = Field(default_factory=list)  # never unasked
    max_tool_calls: int | None = Field(default=None, ge=0)  # calls that really ran in the turn
    forbidden_memory_writes: list[str] = Field(default_factory=list)  # substrings, case-insensitive


class OtherUser(BaseModel):
    """A second user who exists in every store while the case user asks: their data must stay
    theirs. Put `CANARY-...` tokens in it and list them in `canaries.must_not_appear`."""

    model_config = ConfigDict(extra="forbid")

    docs: list[SetupDoc] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)


class SetupWebResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://")
    content: str = Field(min_length=1)


class RedTeamSetup(BaseModel):
    """The world of one attack: files the case user uploaded (`docs`), what a web search returns
    (`web`), memories planted for the user, another user's data, and whether an approval card is
    approved (the search runs, so a poisoned result reaches the model) or rejected (it does not)."""

    model_config = ConfigDict(extra="forbid")

    docs: list[SetupDoc] = Field(default_factory=list)
    web: list[SetupWebResult] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)
    other_user: OtherUser | None = None
    approve_web: bool = False


RedTeamGroup = Literal["injection", "cross_tenant"]


class RedTeamCase(_Record):
    """One attack (doc 09 section 8). `group` says which gated metric the case feeds:
    `injection` -> `safety.injection_resistance`, `cross_tenant` -> `safety.cross_tenant_leaks`.
    `{other_user_id}` in `input` is replaced by the other user's id when the case runs."""

    threat_id: str = Field(pattern=r"^LLM-T\d{2}$")
    input: str = Field(min_length=1)
    should_refuse: bool = False
    canaries: Canaries = Field(default_factory=Canaries)
    setup: RedTeamSetup | None = None
    group: RedTeamGroup | None = None

    @property
    def uses_tools(self) -> bool:
        """True when the case needs the tool-backed run (a world to set up, or a tool to watch)."""
        c = self.canaries
        return self.setup is not None or bool(
            c.forbidden_tool_calls or c.require_approval or c.max_tool_calls is not None
        )

    @model_validator(mode="after")
    def _consistent(self) -> RedTeamCase:
        if "{other_user_id}" in self.input and (
            self.setup is None or self.setup.other_user is None
        ):
            raise ValueError("`{other_user_id}` needs setup.other_user")
        if self.canaries.forbidden_memory_writes and self.setup is None:
            raise ValueError("forbidden_memory_writes needs a setup (the turn it is checked on)")
        return self


class ExistingMemory(BaseModel):
    """A memory the user already has when the turn happens. The id is a local label (`m1`); the
    step maps it to a stable UUID, because the real code only accepts ids it was shown."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^m[0-9]+$")
    content: str = Field(min_length=1)


class ExpectedOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["create", "update", "delete"]
    id: str | None = Field(default=None, pattern=r"^m[0-9]+$")  # update and delete
    content_contains: str | None = Field(default=None, min_length=1)  # case-insensitive

    @model_validator(mode="after")
    def _consistent(self) -> ExpectedOp:
        if (self.op == "create") != (self.id is None):
            raise ValueError("`id` is required for update and delete, and not allowed for create")
        if self.op == "delete" and self.content_contains is not None:
            raise ValueError("a delete has no content")
        return self


class MemoryExtractCase(_Record):
    """One turn for `extract_memories` (doc 09 section 3). `expected_ops` are matched by op type, id
    and a case-insensitive substring of the content; `forbidden` lists substrings (case-insensitive)
    that no applied memory text may contain: sensitive data, instruction text, injected text."""

    user_message: str = Field(min_length=1)
    assistant_message: str = Field(min_length=1)
    existing_memories: list[ExistingMemory] = Field(default_factory=list)
    expected_ops: list[ExpectedOp] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_exist(self) -> MemoryExtractCase:
        known = {m.id for m in self.existing_memories}
        if any(op.id is not None and op.id not in known for op in self.expected_ops):
            raise ValueError("an expected op names a memory that is not in existing_memories")
        return self


class CalibrationCase(_Record):
    """One hand-labelled RAG answer for judge calibration (TASK-037). `label` is the human's
    verdict: `faithful` only if every claim in the answer is supported by the passages."""

    question: str = Field(min_length=1)
    passages: list[str] = Field(min_length=1)
    answer: str = Field(min_length=1)
    label: Literal["faithful", "unfaithful"]
    note: str = ""


# dataset name -> record model. A dataset that is not listed here has no suite yet.
# (`judge.calibration` is not listed: it feeds `evals.calibration`, not the suites, and exists
# only once a person has labelled it.)
MODELS: dict[str, type[_Record]] = {
    "chat.golden": ChatCase,
    "summary.golden": SummaryCase,
    "perf.latency": PerfCase,
    "safety.redteam": RedTeamCase,
    "rag.golden": RagCase,
    "tools.routing": ToolsCase,
    "memory.extract": MemoryExtractCase,
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
