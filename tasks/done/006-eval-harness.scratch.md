# Scratch: 006-eval-harness

> Plan written before any code, per `tasks/AGENTS.md`. Passes: TASK-015 first (it is the larger one and TASK-016 does not depend on it), then TASK-016.

---

## Plan

### TASK-015 Eval runner & two-tier gate
1. Datasets (`evals/datasets/*.jsonl`), drafted by me, **to be reviewed by the developer**: `chat.golden` (30, 8 smoke, includes the three GAP-05 scope cases), `summary.golden` (10, 3 smoke), `perf.latency` (10, 5 smoke), `safety.redteam` (chat-only: rt-06, rt-07, rt-17, rt-19, rt-21 plus a few paraphrases).
2. Judge prompts `backend/prompts/eval.judge.{relevance,refusal,facts}.md` (v1). Faithfulness is not created: it belongs to RAG.
3. `backend/evals/` package (run from `backend/` as `python -m evals.run`):
   - `datasets.py`: load JSONL, validate every record with pydantic (stable unique ids, smoke subset), pin dataset version = sha256 of the file.
   - `metrics.py`: pure scorers (format compliance, percentile, mean, aggregation by metric key); no model calls.
   - `canaries.py`: deterministic canary checks (token present, verbatim n-gram of the system prompt present).
   - `judges.py`: relevance / refusal / fact-retention judges on the `judge` role; strict JSON parse; an unparsable or failed judge call gives `None` (the case is left out of that metric, and the report counts it).
   - `runner.py`: run one case through the real graph (`build_graph`, no checkpointer) with `UsageCollector` and `Tracer.run_config(mode="eval")`, measuring TTFT and cost; n runs per case (1 smoke, 3 full), bounded concurrency.
   - `langfuse_log.py`: best-effort: sync dataset items, one trace per case with its scores (no per-node spans).
   - `run.py`: CLI `--suite smoke|full`, `--runs`, `--out`; writes `evals/results/latest.json` (`{"metrics": ..., "run": ...}`) plus a per-case file; fake mode writes `latest.fake.json`.
4. `evals/eval-baseline.json`: add the **absolute** gate `safety.system_prompt_leaks` (target 0). No regression gate: those need the developer's review of a real full run (ADR-0009).
5. `pyproject.toml`: `pythonpath`, mypy and coverage know `evals`; `tests/evals/` unit tests with fake and scripted judges; add a coverage gate for `backend/evals`.
6. Docs: `evals/AGENTS.md` layout (suite lives in `backend/evals`), `backend/AGENTS.md` structure, `memory/ai.md` (eval status), CHANGELOG.

### TASK-016 Feedback
7. Trace id: `Tracer.trace_id_for(message_id)` = `Langfuse.create_trace_id(seed=message_id)`; the run's callback handler is created with that trace id; `RunService` stores it in `messages.trace_id` (the column exists, nothing wrote it).
8. `db/repositories/feedback.py` (owner-scoped upsert, lookup of ratings for a page of messages), `services/feedback.py` (find an assistant message of the user, save, send the score), `api/feedback.py` (`PUT /messages/{messageId}/feedback`), `Tracer.score_feedback()` (`create_score`, name `user_feedback`, deterministic score id so a re-rating overwrites; comment masked).
9. `MessageOut.feedback` filled from the table; add the router; tracer on `app.state`.
10. Contract test scope (remove the exclusion pattern) + API/isolation/service tests with the real database.
11. Frontend: `features/chat/Feedback.tsx` (thumbs, `aria-pressed`, optional comment is **not** built: the spec allows it, the plan asks only for the rating), `useFeedback` mutation with cache update, Vitest tests, Playwright test in `tests/e2e/chat.spec.ts`.
12. Docs: `memory/api.md`, `memory/ai.md`, CHANGELOG, `frontend/AGENTS.md`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `evals/datasets/{chat.golden,summary.golden,perf.latency,safety.redteam}.jsonl` | create | drafts for developer review |
| `backend/prompts/eval.judge.{relevance,refusal,facts}.md` | create | v1 |
| `backend/evals/{__init__,datasets,metrics,canaries,judges,runner,langfuse_log,run}.py` | create | the suite |
| `backend/tests/evals/*` | create | unit tests |
| `evals/eval-baseline.json`, `evals/AGENTS.md`, `.gitignore` | modify | absolute gate; layout; results ignore |
| `backend/pyproject.toml`, `tests/coverage-baseline.json`, `Makefile` | modify | tooling, gates |
| `backend/app/llm/tracing.py` | modify | trace id, score, `eval.case` run name |
| `backend/app/services/runs.py` | modify | store `trace_id` |
| `backend/app/db/repositories/{feedback,messages}.py`, `backend/app/services/feedback.py`, `backend/app/api/{feedback,chat,schemas,deps}.py`, `backend/app/main.py` | create/modify | feedback endpoint |
| `backend/tests/{api,services,db,llm,contract}/*` | create/modify | tests |
| `frontend/src/features/chat/{Feedback.tsx,api.ts,MessageList.tsx}` + tests, `tests/e2e/chat.spec.ts` | create/modify | thumbs |
| `docs/02_outputs/04_api_spec.yaml` | maybe | only if the implementation shows a gap |
| `memory/{ai,api,_index}.md`, `CHANGELOG.md`, `frontend/AGENTS.md`, `backend/AGENTS.md` | modify | knowledge |

## AI Impact & Eval Plan

- Prompts / models / tools changed: new eval-only judge prompts; no product prompt, model, graph or tool change, so no `make eval` regression is possible yet (this task creates the suite).
- Datasets run: none against a real model (no key). The pipeline is verified with `LLM_MODE=fake`.
- New dataset records: see step 1.
- Tracing: the trace id of a run is derived from the message id (no PII). Feedback comments are `mask`ed before they leave. Eval traces carry the case id and metric scores only.

## Uncertainties

- Whether `CallbackHandler(trace_context={"trace_id": ...})` yields exactly that trace id for a LangGraph run: test with the in-memory exporter before relying on it.
- Langfuse SDK 5.2 replaced the dataset-run-items API with experiments; I will sync dataset items and log traces/scores but not link runs to dataset items (the "runs" comparison view). Unverifiable without keys.
- Real judge outputs: the parsers are tested with scripted replies, not with the real judge model.

## What Will Be Skipped

- TASK-036 (bake-off): needs a real key.
- Regression gates and first measured baselines: need a real full run reviewed by the developer.
- RAG/tools/memory/router datasets and metrics: their features don't exist yet.
- Weekly scheduled workflow: the repo has no CI yet (`.github/` is absent; TASK-003/033/034 in M1 are open).
- Judge calibration (TASK-037), feedback comment input in the UI, removing a rating (not in the spec).

## Risks

- A fake-mode result must never be gated against real baselines: separate output file.
- The absolute gate `safety.system_prompt_leaks` will fail the first real run if the model reveals the system prompt; that is the intent.
- A bug in the handler's trace id would make feedback scores point at nothing: covered by a test.

## Ready to implement?

Yes: the scratch file is complete. (The developer asked for both tasks in one go, so implementation proceeds without a separate review stop.)
