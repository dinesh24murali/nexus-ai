# Task: 036-chat-model-bakeoff

**Status:** done
**Created:** 2026-11-21
**ADR refs:** ADR-0005 (model factory), ADR-0009 (gates)

---

## Goal

Pick the chat model that meets G3 (TTFT), G4 (cost) and the quality targets, and record the measured result (TASK-036 in the implementation plan; split out of `006-eval-harness`).

## Background

Run the chat, tool-routing (as far as available), latency and cost suites on `gpt-6.1-terra` (effort `low`) and the pinned `gpt-5.4-mini-2026-03-17`; re-check model names and prices first. Re-run the tool-routing part in M4. The eval runner and the first baselines exist (TASK-015): `gpt-6.1-terra` at effort low measured format compliance 0.985, TTFT p95 1472 ms and $0.0009 per turn (`memory/ai.md`).

## Acceptance Criteria

- [x] Decision recorded in `memory/ai.md` with the measured TTFT p95, cost per turn and scores
- [x] `evals/results/bakeoff-2026-11-21.md` written (the results folder is gitignored); `.env.example` unchanged because the default model stays

## Dependencies

- **Tasks:** TASK-015, TASK-014 (done)
- **Memory files:** `memory/ai.md`
- **Docs:** `09_eval_plan.md`, `08_ai_system_design.md` §3

## AI Impact

- **Models / params changed:** possibly `LLM_CHAT_MODEL` / effort
- **Eval datasets affected:** `chat.golden`, `perf.latency` (and `tools.routing` once it exists)

## Notes

- Result: kept `gpt-6.1-terra`; see `memory/ai.md`. Tool-routing part to be re-run in M4.
- Changing the chat model changes gated metrics: raise or keep gates per ADR-0009 and note it in `CHANGELOG.md`.
- Write `tasks/active/036-chat-model-bakeoff.scratch.md` before running anything.
