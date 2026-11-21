# Task: 025-memory-store-extraction

**Status:** done
**Created:** 2026-11-21
**ADR refs:** ADR-0003 (memories in Postgres), ADR-0007 (job queue), ADR-0009 (gate tiers)

---

## Goal

Nexus learns durable facts about a user from what the user says: a background job reads each completed turn, asks a small model for create/update/delete operations, filters them (sensitive categories, instruction-like text, near-duplicates) and writes the survivors to a per-user memory store.

## Background

TASK-025 in `docs/02_outputs/05_implementation_plan.md` (M5). Design: `docs/02_outputs/08_ai_system_design.md` §7 (memory design) and §9 (jobs); threat LLM-T04 (memory poisoning) in `docs/02_outputs/10_llm_threat_model.md`; evals in `docs/02_outputs/09_eval_plan.md` (`memory.extract`, `memory.sensitive_refusal`). Reading memories into the prompt is TASK-026, the API and UI TASK-027; this task only writes.

## Acceptance Criteria

- [x] `AsyncPostgresStore` with namespace `("memories", user_id)` and an embedding index; created by `python -m app.migrate`
- [x] `extract_memories` job enqueued after each completed turn (idempotent on the assistant message id), strict structured output, at most 3 ops per turn, ops may only touch memories the model was shown
- [x] Deny-list of sensitive categories and instruction-like content rejected (post-filter in code, not only in the prompt)
- [x] A `create` with similarity >= 0.9 to an existing memory is dropped; 200-item cap evicts the least recently used
- [x] Nothing is stored when memory is disabled (user setting or `MEMORY_WRITES_DISABLED`), checked again when the job runs
- [x] An explicit "remember X" in the user's message ends as exactly one memory (fake LLM, real Postgres)
- [x] Tenant isolation test: user B's search/update/delete never sees or touches user A's memories
- [x] Extraction spend (model + embeddings) recorded in `usage_daily` with `requests=0`; job traced as `job.extract_memories`
- [x] `memory.extract` dataset (25 records) with `memory.extract_precision`, `memory.extract_recall`, `memory.sensitive_refusal`; runs offline in fake mode
- [-] `memory.sensitive_refusal = 1` measured on the real model and extraction baselines recorded (needs paid runs and developer review of the records; see `tasks/deferred.md`)

## Dependencies

- **Tasks:** TASK-012 (queue), TASK-010 (graph/llm factory)
- **Memory files:** `memory/ai.md` (Long-term Memory, Background Jobs), `memory/api.md`
- **Docs:** doc 08 §7 and §9, doc 09 (`memory.extract`), doc 10 (LLM-T04, LLM-T06)

## AI Impact

- **Prompts touched:** new `nexus.memory_extract` v1
- **Models / params changed:** role `memory_extract` (`gpt-6-luna`, effort none, 600 output tokens, 30 s) is used for the first time; embeddings `text-embedding-3-small` for the store index
- **Graph / tools / retrieval changes:** none in the graph; new background job and store
- **Eval datasets affected / added:** `evals/datasets/memory.extract.jsonl` (new)
- **Threat model entries:** LLM-T04, LLM-T06
- **Expected cost / latency delta:** one `gpt-6-luna` call (~500 input tokens) plus up to 4 embeddings per turn, in the background; none on the request path

## Files Expected to Change

See `025-memory-store-extraction.scratch.md`.

## Notes

- Memory text is untrusted: it goes into prompts only as data (TASK-026), is never logged, and never put in a trace unmasked.
- LangGraph's store creates its own tables (`python -m app.migrate`), not Alembic.
