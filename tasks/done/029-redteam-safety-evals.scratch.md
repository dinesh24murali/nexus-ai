# Scratch: 029-redteam-safety-evals

## Plan

1. **Record schema** (`backend/evals/datasets.py`): `RedTeamCase` gains `setup` (`docs` the user uploaded, `web` the results a search returns, `memories` planted for the user, `other_user{docs, memories}`, `approve_web`), `group` (`injection` | `cross_tenant`, which metric the case feeds), and the canaries gain `forbidden_tool_calls`, `require_approval`, `max_tool_calls`, `forbidden_memory_writes`. Validation: a case with tool canaries or a setup runs through tools; `{other_user_id}` is substituted in the input.
2. **Step** (`backend/evals/redteam.py`): a case with a setup or tool canaries runs through the REAL graph with the real model and stand-in tools (`evals/tools.py` machinery): a tenant-aware file store holding both users' documents and recording the user id of every search, a tenant-aware memory lookup (planted memories), a scripted web searcher, approvals answered with approve or reject per case. Scoring (all deterministic): canary tokens in the answer (and, for cross-tenant, in tool results), forbidden tool calls, a web search that ran without approval, executed tool calls over the cap, a lookup made for another user's id, and (rt-05, rt-09) the extraction plan for the turn (real `plan_ops`) writing forbidden text.
3. **Runner wiring** (`evals/run.py`): `safety.redteam` takes the tools graph factory and the memory model; chat-only cases keep the existing path; new metrics `safety.injection_resistance` (mean over group `injection`) and `safety.cross_tenant_leaks` (total over group `cross_tenant`); `main_async` builds the memory model when `safety.redteam` is selected too.
4. **Dataset**: draft the records (10 -> 40): rt-01a-c, 02a-b, 03a-b, 04a-b, 05a-b, 08a-b, 09a-c, 10a-b, 11a-b, 12a-b, 13a-b, 14a-b, 16a-b, 20a-b with unique `CANARY-<hex>` tokens; 12 smoke.
5. **Gates**: add the two absolute gates to `evals/eval-baseline.json` (targets exist; add to `metrics` at 0.95 / smoke 0.9 and 0), noting they are not yet measured on a real model.
6. **Tests** (`backend/tests/evals/test_redteam_step.py`): dataset shape and sizes; a *vulnerable* scripted model (obeys the payload, calls forbidden tools, searches for another user) makes every canary fire and the metrics drop; a *resistant* one passes everything; cross-tenant stub really filters (and a leaking stub is caught); approval/cap logic; memory-write check with a scripted extraction model (rt-05, rt-09), including the real `memory.policy` filter; runner integration in `LLM_MODE=fake`; update dataset size tests.
7. Docs: doc 09 section 8 (which rt records exist and where the rest went), doc 10 test/eval references, `evals/AGENTS.md`, `memory/ai.md` (eval status), `CHANGELOG.md`, `tasks/deferred.md` (D13 partly closed, D17 note, new D19), plan checkboxes, move task + scratch to `tasks/done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/evals/datasets.py` | modify | setup + new canaries on `RedTeamCase` |
| `backend/evals/canaries.py` | modify | tool, approval, cap and tenant checks |
| `backend/evals/redteam.py` | create | the tools-backed red-team step |
| `backend/evals/tools.py` | modify | `run_one` takes the approval decision and user id; keeps the full tool result text and the executed-call count |
| `backend/evals/run.py` | modify | wiring, new metrics, memory model for red team |
| `evals/datasets/safety.redteam.jsonl` | modify | ~30 new records (developer reviews them) |
| `evals/eval-baseline.json` | modify | two absolute gates |
| `backend/tests/evals/test_redteam_step.py`, `test_datasets.py`, `test_run.py` | create/modify | |
| `docs/02_outputs/09_eval_plan.md`, `10_llm_threat_model.md`, `evals/AGENTS.md`, `memory/ai.md`, `CHANGELOG.md`, `tasks/deferred.md`, plan | modify | |

## AI Impact & Eval Plan

- Changed: eval code and dataset only; no prompt, model, tool or graph change.
- Run: unit tests with scripted models (free). `make eval-smoke` / `make eval` on the real model NOT run (no paid run authorised): deferred as D19 with the first-run checklist.
- New records: ~30 (above). Tracing: the step logs through the existing `EvalLogger`; records hold only synthetic canaries, never real data.

## Uncertainties

- Whether a faithful answer may *quote* an injected token while refusing it (a false failure): tokens are unique `CANARY-...` strings, so a quote is rare; the first real run will show it and the developer decides per record.
- rt-20 (hidden DOCX text): the stand-in file search serves text, so this tests the model's behaviour on the hidden text, not the DOCX parser (parser behaviour is in `tests/rag`).
- Whether the real model searches at all for the "summarise my doc" records (D3: search-skipping). A run that never reads the poisoned document passes trivially; the step records `searched` in the case notes so the review can see it.

## What Will Be Skipped

- **rt-22, rt-23, rt-25** (router manipulation, PDF renderer, research exfiltration): their features are M7 (v1.1); they are added with those tasks.
- **rt-15** (HTML in the DOM): a DOM property, already covered by `Markdown.test.tsx` and `tests/e2e/chat.spec.ts`; the doc 10 row points there.
- **rt-18** (not-in-corpus question): scored by `rag.unanswerable_accuracy` in `rag.golden`, which already has 7 such cases.
- The real-model run and the fixes it may drive (D19).

## Risks

- A blind check would let a gate pass for the wrong reason: the vulnerable-model test is the guard (every canary must fire against a model that obeys).
- A tenant stub that cannot leak would test nothing: the stub keeps both users' data in one store and filters by the id it is given, and a test feeds it a wrong id.
- Absolute gates recorded but never measured on the real model make `make eval` fail until the first run is done (like `memory.sensitive_refusal`): intended, stated in the gate's note.

## Ready to implement?

Yes.
