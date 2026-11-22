# evals/ — AGENTS.md

## Purpose

LLM behaviour is nondeterministic and can regress silently when a prompt, model, graph or retrieval setting changes. This folder is the **quality gate for AI behaviour**, the way `tests/coverage-baseline.json` is the gate for code coverage.

## Directory Layout

```
evals/
├── eval-baseline.json   ← `targets` (informational) + `metrics` (gates); `make eval` fails if a result misses a gate
├── check_baseline.py    ← Stack-agnostic gate: compares results/latest.json to the baseline
├── datasets/            ← Versioned golden datasets (*.jsonl), one record per line
├── fixtures/docs/       ← The fictional RAG corpus (source text + manifest.json; PDF/DOCX are built at run time)
└── results/             ← Eval run outputs (gitignored); the suite writes results/latest.json
                           (LLM_MODE=fake writes latest.fake.json instead, which the gate never reads)
```

The eval **suite** itself (the code that runs the app against datasets and scores outputs) lives in `backend/evals/` (`run.py` CLI, `datasets.py` record schemas, `metrics.py`, `judges.py`, `canaries.py`, `runner.py`, `langfuse_log.py`, `calibration.py` (`python -m evals.calibrate`: Cohen's κ of the faithfulness judge against hand labels in `datasets/judge.calibration.jsonl`); tests in `backend/tests/evals/`) and is invoked via `EVAL_COMMAND` / `EVAL_SMOKE_COMMAND` in the `Makefile`. `make eval` needs `OPENAI_API_KEY`. `rag.golden` ingests the fixture corpus into a throwaway Qdrant collection, so **a run that includes it needs Qdrant** (`make up` / `docker compose up -d qdrant`); `--no-rag` skips it, `--dataset rag.golden` runs only it. `safety.redteam` records that need a world (`setup`) or a tool canary run in `backend/evals/redteam.py` through the real graph with stand-in tools and are scored by deterministic canaries (`canaries.py`); every new attack needs a threat id, a unique `CANARY-<hex>` token, and a test that a model obeying it trips the canary. A dataset needs a record model in `backend/evals/datasets.py` (unknown fields are rejected) and a step in `evals/run.py`. A metric with no samples is left out of the results, so a gated metric that could not be measured fails the gate. It must write `evals/results/latest.json`:

```json
{"metrics": {"rag.faithfulness": 0.91, "tools.selection_accuracy": 0.95}}
```

## Source of Truth

- Metrics, targets, gate types and datasets come from `docs/02_outputs/09_eval_plan.md`
- Gate tiers follow ADR-0009: **absolute** gates (safety, HITL) are fixed; **regression** gates are the measured first-run values and only ratchet up; **advisory** metrics (uncalibrated LLM judges) are reported, never gated
- Red-team records reference threat IDs from `docs/02_outputs/10_llm_threat_model.md` (field `"threat_id": "LLM-T03"`)

## Dataset Conventions

- File name: `<feature>.<purpose>.jsonl` (e.g. `rag.golden.jsonl`, `safety.redteam.jsonl`)
- Every record has an `id` (stable, never reused) and `input`; add `expected`, `reference_context`, `expected_tool`, `threat_id` as the metric needs
- Keep a small subset tagged `"smoke": true` for PR runs (budget: < 2 minutes, < $0.50)
- AI tools draft dataset records; the developer reviews every record before it is committed
- Never put real user data or secrets in datasets; mined production traces must be anonymised first
- Mirror datasets into the observability platform (e.g. Langfuse datasets) so runs are comparable over time

## When to Run

| Change | Command |
|--------|---------|
| Any prompt, model, reasoning effort, tool description, graph edge, chunking or retrieval change | `make eval` |
| Every PR (CI) | `make eval-smoke` |
| Before a release | `make eval` on the full datasets |

## Rules for AI Tools

- When a run misses a gate, fix the prompt/code. Lowering a gate needs a note in the task file and developer approval
- Record a new regression gate only after the developer has reviewed the suite's first full run; raise gates after genuine improvements and note it in `CHANGELOG.md`
- Unit tests must use a fake/stub LLM; real model calls belong in evals, not in `tests/`
- When adding a new AI feature, add its dataset and metric(s) before marking the task done
- Every bug found in production AI behaviour becomes a new dataset record
