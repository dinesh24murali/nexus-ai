# Task: 005-core-chat

**Status:** active (this pass: TASK-009 and TASK-010 only; TASK-011 … TASK-014 remain)
**Created:** 2026-11-21
**ADR refs:** ADR-0002 (graph, checkpointer), ADR-0005 (model factory), ADR-0008 (repo-canonical prompts), ADR-0004 (tracing, redaction), ADR-0009 (coverage gates)

---

## Goal

Build the AI foundation for chat: a single model factory with a scripted fake model, prompt loader, tracing/redaction/pricing helpers (TASK-009), and the tool-less LangGraph chat graph with trimming/summarisation and a cache-friendly prompt layout (TASK-010).

## Background

Implementation plan v1.3 (`docs/02_outputs/05_implementation_plan.md`), milestone M2, roadmap group `005-core-chat`. This pass covers **TASK-009** and **TASK-010**. The SSE endpoint (TASK-011), job queue (TASK-012), chat UI (TASK-013) and budgets (TASK-014) are out of scope. M1 (`004-skeleton`) still has TASK-003/033/034 open; they are not touched here.

## Acceptance Criteria

Copied from the plan.

**TASK-009 Model factory, fake model, prompt loader, tracing**
- [x] Unit tests with the fake model; the factory never sends `temperature` with an effort other than `none`
- [x] `mask` unit tests cover emails, phone numbers, card/IBAN-like numbers, JWTs and API keys
- [x] Verify whether multi-turn tool calls with `store=false` need encrypted reasoning items; record the answer in `memory/ai.md` *(answer: yes. Verified from `langchain-openai` source and OpenAI docs/forum; NOT by a live API call, `OPENAI_API_KEY` is empty locally. The first real tool loop in M3/M4 confirms it.)*

**TASK-010 Chat graph v1**
- [x] Graph unit tests with the fake model cover a multi-turn thread and summarisation triggering
- [x] A test asserts the message prefix is byte-identical across two consecutive turns (cache-friendliness)

## Dependencies

- **Tasks:** TASK-004, TASK-005 (done, in `004-skeleton`)
- **Memory files:** `memory/ai.md`, `memory/project.md`
- **Docs:** `08_ai_system_design.md` §2–§4, §10; `09_eval_plan.md` (no suite yet); `10_llm_threat_model.md` (no new tools)

## AI Impact

- **Prompts touched:** new `nexus.system` v1, `nexus.context` v1, `nexus.summarize` v1 (`nexus.title` arrives with TASK-012)
- **Models / params changed:** factory defines every role (chat, summarize, title, memory_extract, judge, router, long_form) from env; no real model is called in this pass
- **Graph / tools / retrieval changes:** `load_context → (summarize?) → agent → END`, no tools yet
- **Eval datasets affected / added:** none — the eval harness is TASK-015, so `make eval` cannot run yet (`evals/eval-baseline.json` `metrics` is empty, nothing to regress)
- **Threat model entries:** none (no new tools). Untrusted-data policy for `<summary>` / `<user_memory>` lives in `nexus.system`
- **Expected cost / latency delta:** none until TASK-011 wires it to a request

## Files Expected to Change

See `005-core-chat.scratch.md`.

## Progress log

- 2026-11-21 **TASK-009 done.** `app/llm/`: `factory.py` (7 roles, Responses API, `store=False`, `include=["reasoning.encrypted_content"]` when effort ≠ `none`, temperature only at `none`, fallback after tool binding, router without retries/fallback, ruff `TID251` ban on constructing provider clients elsewhere), `fake.py` (+ JSON fixtures), `prompts.py` (+ `prompt_sync.py`, `scripts/sync_prompts.py`), `tracing.py` (Langfuse v5; masked; per-node spans verified with an in-memory exporter), `redaction.py`, `pricing.py` (Decimal), `tokens.py`. Prompts `nexus.system|context|summarize` v1. Config gained the `LLM_*` settings and `OPENAI_API_KEY`.
- 2026-11-21 **TASK-010 done.** `app/graph/`: `load_context → (summarize?) → agent → END`, injectable models, cache-friendly request layout, summarisation at 24k keeping ≤ 12k aligned on a human message. Integration test against the Postgres checkpointer with `LANGGRAPH_STRICT_MSGPACK=true`, including an AI message with an encrypted reasoning block round-tripping intact.
- Verification: `ruff`, `ruff format`, `mypy --strict` clean; 422 backend tests pass (unit + integration against the Compose Postgres on 5433). New packages: `app/llm` 95.2%, `app/graph` 91.7% (statements + branches), recorded as gates. Backend image rebuilt and checked offline as the non-root user (prompts load, tiktoken encoding baked in, fake graph answers).
- Mutation checks (change the code, the test must fail): temperature rule (found the first tests did NOT cover it: no real role pairs a temperature with an effort; added direct tests, now 4 failures), prompt-sync version-bump guard, summariser `nostream` tag, prefix stability (two mutations).
- **Found and fixed while testing:** the first `temperature` tests passed against a mutated factory (gap above); a test helper matched `<context>` inside the system prompt text.
- **Not done / honest limits:** `make eval` cannot run (eval harness is TASK-015; `evals/eval-baseline.json` has no gates), so no AI-behaviour regression gate exists yet. No real model call and no live Langfuse trace were made. Frontend untouched, so `make verify` was not run. For several small modules (pricing, redaction, prompts, tokens, prompt_sync) tests and code were written in the same step rather than with an observed red run; their rules were then protected with mutation checks where they matter.

## Notes

- Not moved to `tasks/done/` after this pass: the group (TASK-011 … TASK-014) is unfinished.
