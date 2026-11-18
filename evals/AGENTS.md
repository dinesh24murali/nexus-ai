# evals/ — AGENTS.md

## Purpose

LLM behaviour is nondeterministic and can regress silently when a prompt, model, graph or retrieval setting changes. This folder is the **quality gate for AI behaviour**, the way `tests/coverage-baseline.json` is the gate for code coverage.

## Directory Layout

```
evals/
├── eval-baseline.json   ← Metric thresholds; `make eval` fails if results fall below them
├── check_baseline.py    ← Stack-agnostic gate: compares results/latest.json to the baseline
├── datasets/            ← Versioned golden datasets (*.jsonl), one record per line
└── results/             ← Eval run outputs (gitignored); the suite writes results/latest.json
```

The eval **suite** itself (the code that runs the app against datasets and scores outputs) lives with the backend and is invoked via `EVAL_COMMAND` / `EVAL_SMOKE_COMMAND` in the `Makefile`. It must write `evals/results/latest.json`:

```json
{"metrics": {"rag.faithfulness": 0.91, "tools.selection_accuracy": 0.95}}
```

## Source of Truth

- Metrics, thresholds and datasets come from `docs/02_outputs/09_eval_plan.md`
- Red-team records reference threat IDs from `docs/02_outputs/10_llm_threat_model.md` (field `"threat_id": "LLM-T03"`)

## Dataset Conventions

- File name: `<feature>.<purpose>.jsonl` (e.g. `rag.golden.jsonl`, `safety.redteam.jsonl`)
- Every record has an `id` (stable, never reused) and `input`; add `expected`, `reference_context`, `expected_tool`, `threat_id` as the metric needs
- Keep a small subset tagged `"smoke": true` for PR runs (budget: < 2 minutes, < $0.50)
- Never put real user data or secrets in datasets; mined production traces must be anonymised first
- Mirror datasets into the observability platform (e.g. Langfuse datasets) so runs are comparable over time

## When to Run

| Change | Command |
|--------|---------|
| Any prompt, model, temperature, tool description, graph edge, chunking or retrieval change | `make eval` |
| Every PR (CI) | `make eval-smoke` |
| Before a release | `make eval` on the full datasets |

## Rules for AI Tools

- Do **not** lower a threshold in `eval-baseline.json` to make a run pass. Fix the prompt/code, or record the trade-off in the task file and get developer approval
- Raising a threshold after a genuine improvement is encouraged; note it in `CHANGELOG.md`
- Unit tests must use a fake/stub LLM; real model calls belong in evals, not in `tests/`
- When adding a new AI feature, add its dataset and metric(s) before marking the task done
- Every bug found in production AI behaviour becomes a new dataset record
