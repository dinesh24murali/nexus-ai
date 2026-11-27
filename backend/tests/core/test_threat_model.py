"""Every threat in doc 10 has a mitigation and a test that exists (TASK-030).

The traceability table at the end of `docs/02_outputs/10_llm_threat_model.md` names, per threat,
the tests that fail when its mitigation breaks. This reads the table, so a threat added to the
threat table without a row, or a test that was renamed or deleted, fails the build instead of
leaving a reference that points at nothing.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "02_outputs" / "10_llm_threat_model.md"
REDTEAM = ROOT / "evals" / "datasets" / "safety.redteam.jsonl"

pytestmark = pytest.mark.skipif(not DOC.is_file(), reason="repository root not available")

_ROW = re.compile(r"^\| (LLM-T\d+) \| (.+) \| (.*) \|$")
_PATH_REF = re.compile(r"`((?:backend|frontend|tests|evals)/[^`\s]+?)(?:::(\w+))?`")
_REDTEAM_REF = re.compile(r"\b(rt-\d+)\b")
_VALID_STATUS = {"built", "M7"}


def _section(heading: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading) :], re.M)
    return text[start : start + len(heading) + nxt.start()] if nxt else text[start:]


def _threat_ids() -> set[str]:
    """The ids in the threat table (section 2): its rows have ten columns, the review's three."""
    ids = set()
    for line in _section("## 2. Threat Table").splitlines():
        if m := re.match(r"^\| (LLM-T\d+) \|", line):
            ids.add(m.group(1))
    return ids


def _review_rows() -> dict[str, tuple[str, str]]:
    rows = {}
    for line in _section("## Security review (TASK-030").splitlines():
        if m := _ROW.match(line):
            rows[m.group(1)] = (m.group(2).strip(), m.group(3))
    return rows


def _redteam_threats() -> dict[str, str]:
    """Record id -> threat id. A record id is the attack (`rt-01`) plus a variant letter."""
    records = (json.loads(line) for line in REDTEAM.read_text().splitlines() if line.strip())
    return {r["id"]: r["threat_id"] for r in records}


def test_the_threat_table_is_read_and_has_the_threats_the_review_covers() -> None:
    ids = _threat_ids()
    assert {"LLM-T01", "LLM-T15", "LLM-T16", "LLM-T17", "LLM-T20"} <= ids
    assert "LLM-T19" not in ids  # retired in revision 1.3


def test_every_threat_has_exactly_one_row_in_the_review_and_no_row_is_invented() -> None:
    assert set(_review_rows()) == _threat_ids()


def test_every_row_has_a_known_status() -> None:
    for threat, (status, _) in _review_rows().items():
        assert status in _VALID_STATUS, f"{threat}: status {status!r}"


def test_a_built_threat_names_at_least_one_test_that_is_not_only_a_red_team_record() -> None:
    for threat, (status, refs) in _review_rows().items():
        if status == "built":
            assert _PATH_REF.search(refs), f"{threat} is built but names no test file"


def test_a_threat_of_a_later_milestone_names_the_task_that_will_test_it() -> None:
    for threat, (status, refs) in _review_rows().items():
        if status == "M7":
            assert re.search(r"TASK-\d+", refs), f"{threat}: name the M7 task"


def test_every_named_test_file_and_test_exists() -> None:
    missing = []
    for threat, (_, refs) in _review_rows().items():
        for path, name in _PATH_REF.findall(refs):
            file = ROOT / path
            if not file.is_file():
                missing.append(f"{threat}: {path}")
            elif name and not re.search(
                rf"\b(def|async def) {name}\b|['\"`]{name}['\"`]", file.read_text()
            ):
                missing.append(f"{threat}: {path}::{name}")
    assert not missing, "references to tests that do not exist:\n" + "\n".join(missing)


def test_every_named_red_team_attack_exists_and_is_filed_under_that_threat() -> None:
    """A built row may only cite an attack (`rt-01` = records `rt-01a`, `rt-01b`...) that is in
    the dataset with this threat id. Rows of a later milestone cite records not written yet."""
    records = _redteam_threats()
    wrong = []
    for threat, (status, refs) in _review_rows().items():
        if status != "built":
            continue
        for ref in _REDTEAM_REF.findall(refs):
            threats = {t for rid, t in records.items() if re.fullmatch(rf"{ref}[a-z]", rid)}
            if threats != {threat}:
                wrong.append(f"{threat}: {ref} is filed under {sorted(threats) or 'nothing'}")
    assert not wrong, "red-team references that do not match the dataset:\n" + "\n".join(wrong)


def test_the_check_notices_a_reference_to_a_test_that_is_gone() -> None:
    sample = "`backend/tests/core/test_threat_model.py::test_no_such_test`"
    ((path, name),) = _PATH_REF.findall(sample)
    text = (ROOT / path).read_text()
    assert not re.search(rf"\b(def|async def) {name}\b", text)
