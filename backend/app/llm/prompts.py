"""Prompt loader (ADR-0008): `backend/prompts/<name>.md` is the only source of prompt text.

Each file starts with YAML front-matter holding `name` (must equal the file stem) and an integer
`version` (bumped on every change), plus optional extra keys such as `min_confidence`. Variables
are `{{name}}` placeholders; rendering is strict (missing or unexpected variables raise) and
single-pass, so substituted values are never interpreted again.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    body: str
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def variables(self) -> frozenset[str]:
        return frozenset(_VARIABLE.findall(self.body))

    def render(self, **values: object) -> str:
        expected = self.variables
        missing = expected - values.keys()
        unexpected = values.keys() - expected
        if missing:
            raise PromptError(f"{self.name}: missing variables {sorted(missing)}")
        if unexpected:
            raise PromptError(f"{self.name}: unexpected variables {sorted(unexpected)}")
        return _VARIABLE.sub(lambda m: str(values[m.group(1)]), self.body)


def _parse(name: str, text: str) -> Prompt:
    match = _FRONT_MATTER.match(text)
    if not match:
        raise PromptError(f"{name}: missing front-matter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise PromptError(f"{name}: invalid front-matter") from exc
    if not isinstance(meta, dict):
        raise PromptError(f"{name}: invalid front-matter")
    if meta.get("name") != name:
        raise PromptError(
            f"{name}: front-matter name {meta.get('name')!r} must equal the file name"
        )
    version = meta.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PromptError(f"{name}: front-matter version must be a positive integer")
    body = match.group(2).strip()
    if not body:
        raise PromptError(f"{name}: prompt body is empty")
    extra = {k: v for k, v in meta.items() if k not in ("name", "version")}
    return Prompt(name=name, version=version, body=body, meta=extra)


@cache
def _load(name: str, directory: Path) -> Prompt:
    path = directory / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"prompt not found: {name}")
    return _parse(name, path.read_text(encoding="utf-8"))


def load_prompt(name: str, *, directory: Path | None = None) -> Prompt:
    if not _NAME.match(name) or len(name) > 64:
        raise PromptError(f"invalid prompt name: {name!r}")
    return _load(name, directory or PROMPTS_DIR)


def all_prompts(directory: Path | None = None) -> list[Prompt]:
    base = directory or PROMPTS_DIR
    return [load_prompt(path.stem, directory=base) for path in sorted(base.glob("*.md"))]
