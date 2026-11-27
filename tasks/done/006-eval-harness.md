# Task: 006-eval-harness

**Status:** done (TASK-015, TASK-016). TASK-036 was split out into `tasks/active/036-chat-model-bakeoff.md`
**Created:** 2026-11-21
**ADR refs:** ADR-0004 (tracing, redaction), ADR-0008 (repo-canonical prompts), ADR-0009 (two-tier gates, coverage)

---

## Goal

Build the eval runner and its two-tier gate (TASK-015) so AI behaviour has a test of its own, then add message feedback that reaches the Langfuse trace of the rated answer (TASK-016).

## Background

Implementation plan v1.3 (`docs/02_outputs/05_implementation_plan.md`), milestone M2, roadmap group `006-eval-harness`. The group holds TASK-015, TASK-036 (chat-model bake-off) and TASK-016. This pass covers **TASK-015** and **TASK-016**. **TASK-036 is left out on purpose:** it compares real models (TTFT, cost, quality) and was not asked for in this pass; it needs the baselines from TASK-015, which now exist. It continues in `tasks/active/036-chat-model-bakeoff.md`.

The suite can run only the features that exist today: chat, summarisation, latency/cost and the chat-only red-team cases. RAG, tools, memory and router datasets arrive with their own tasks (doc 09 §2).

## Acceptance Criteria

Copied from the plan.

**TASK-015 Eval runner & two-tier gate**
- [x] `make eval-smoke` runs in < 2 min and gates on `eval-baseline.json` *(real model: smoke ~42 s, full ~2m40s; `check_baseline.py` passes. The gate holds one absolute metric, `safety.system_prompt_leaks`)*
- [x] Chat datasets are seeded and reviewed; first measured baselines recorded *(seeded: yes, 60 records. **Developer accepted the datasets as they are (2026-11-21).** First measured baselines: recorded 2026-11-21 after the developer accepted the numbers: `chat.format_compliance`, `ops.ttft_p95_ms`, `ops.cost_per_turn_usd` plus the absolute `safety.system_prompt_leaks`)*

**TASK-016 Feedback**
- [x] The score is visible on the Langfuse trace *(checked live: a trace with the derived id got one `user_feedback` score, and re-rating overwrote it instead of adding a second; read back through the API, not the UI)*

Extra criteria this pass adds (from the repo rules, not the plan):
- [x] `PUT /messages/{id}/feedback` is owner-scoped (another user's message is a 404), overwrites a previous rating, and has a contract test
- [x] `Message.feedback` in the history reflects the user's rating
- [x] Thumbs up/down in the chat UI with a unit test and a Playwright test *(7 Vitest cases, 1 Playwright test; `make verify`: 18 e2e tests and the DOM checks pass)*
- [x] Coverage gates not lowered; new package gates recorded *(`app/llm` 97, `features/chat` 88, new `backend/evals` 96)*

## Dependencies

- **Tasks:** TASK-010 (graph), TASK-011 (runs, messages), TASK-014 (budgets)
- **Memory files:** `memory/ai.md`, `memory/api.md`
- **Docs:** `09_eval_plan.md`, `08_ai_system_design.md` §10, `04_api_spec.yaml` (`putFeedback`), `10_llm_threat_model.md` (no new tools)

## AI Impact

- **Prompts touched:** new `eval.judge.relevance`, `eval.judge.refusal`, `eval.judge.facts` (v1, evals only); no product prompt changes
- **Models / params changed:** none; the judge role (`LLM_JUDGE_MODEL`, effort `medium`) already exists in the factory
- **Graph / tools / retrieval changes:** none. Tracing: the Langfuse trace id of a run is now derived from the assistant message id so feedback can find it
- **Eval datasets affected / added:** `chat.golden`, `summary.golden`, `perf.latency`, `safety.redteam` (chat-only subset)
- **Threat model entries:** LLM-T03 (system prompt leak), LLM-T14, LLM-T11, LLM-T06/T16 through the chat-only red-team records; no new tool
- **Expected cost / latency delta:** none for users. Smoke target < $0.50 (unmeasured)

## Files Expected to Change

See `006-eval-harness.scratch.md`.

## Decisions and open points

- Rating a user message, someone else's message or an unknown id is the same 404. The score must be a strict integer (`true` and `"1"` are 422).
- A rating cannot be withdrawn (the spec has no operation for it), so the UI ignores a second press on the chosen thumb. The optional comment is accepted by the API but the UI does not ask for one.
- The Langfuse score is sent once, after the rating is stored. If Langfuse is down at that moment the score is lost (the database keeps the rating); nothing retries it.
- Answers written before this change, and `failed` answers written by the reaper, have no trace id: they can be rated but nothing is sent to Langfuse.
- `safety.canary_pass_rate` is a metric this runner adds (informational; doc 09 has no key for the rt-19/rt-21 canary checks until `safety.injection_resistance` has injection cases).
- Dataset records are drafts. Several are fragile for a real model (for example `chat-023` expects the word "calor", `chat-027` has no check at all): accepted by the developer as they are; `chat-017` and `chat-027` still disagree with the judge, which is an advisory signal only.
- The weekly workflow is not written: the repo has no CI yet (TASK-003/033/034, M1).
- OpenAI rejects some harmful prompts with a 400 before any answer; the eval counts that as a refusal, but the app shows an error. Open product question.
- To run the e2e tests I started the stack in fake mode with a temporary Compose override outside the repo, then put it back to the `.env` mode.

## Notes

- Fake-mode runs write `evals/results/latest.fake.json`, never `latest.json`, so a fake result can't be gated against real baselines by mistake.
- Judged metrics stay advisory (ADR-0009).
