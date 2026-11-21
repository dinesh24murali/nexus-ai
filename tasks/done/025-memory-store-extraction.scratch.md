# Scratch: 025-memory-store-extraction

## Plan

1. **Store** (`app/memory/store.py`): `MemoryStore` wraps `AsyncPostgresStore` with `index={dims: 1536, embed: <async fn over the factory's embeddings client>, fields: ["content"]}` and cosine distance. One store per job (its own connection), so the embedding-token counter of a job is not mixed with another's (the store batches calls on a background task, so a contextvar would not work). Methods, all taking `user_id` and building the namespace `("memories", str(user_id))` internally: `search(user_id, query, limit)` (content, id, score), `recent(user_id, limit)`, `get`, `create`, `update`, `delete`, `count`, `evict_to(user_id, cap)`. Value: `{content, category, source, conversation_id, created_at, updated_at, last_used_at}`.
2. **Migrate**: `setup_langgraph` builds the store with the same index config so the vector table exists (needs pgvector; the Compose image has it).
3. **Policy** (`app/memory/policy.py`, pure): `check(content) -> Rejection | None` with the sensitive deny-list (health, religion, sexuality, politics, precise location, credentials and financial identifiers; regexes, tested with positive and negative examples) and instruction-like text (starts with an imperative addressed to the assistant, or "ignore previous", "from now on", "system prompt", ...); length 1..500, no control characters. Returns a reason code only (never the text).
4. **Extraction** (`app/memory/extract.py`): `MemoryOps` pydantic schema (strict, all fields required, nullable where unused); `render_prompt` (existing memories and the turn wrapped as data); `plan_ops(model, user_text, assistant_text, existing)` calls the model with `with_structured_output(MemoryOps, method="json_schema", strict=True)` and returns the validated, filtered, capped list (max 3; `update`/`delete` only for ids that were shown; `create` content run through the policy). Pure of the store so the eval can call it. `apply_ops(store, ...)` does the dedupe (>= 0.9 similarity to an existing memory drops a create; an update of a memory that vanished is skipped), writes, and enforces the 200 cap.
5. **Prompt** `backend/prompts/nexus.memory_extract.md` v1: what to keep (stable preferences, facts, goals, context), what never (sensitive list, secrets, one-off requests, anything the assistant said, instructions), explicit "remember X" is kept, update when a fact changed, delete when the user retracts, max 3 ops, `{}` ops when nothing. Inputs: `existing_memories`, `user_message`, `assistant_message` as data blocks.
6. **Job** `extract_memories` in `jobs/handlers.py` (timeout 120 s, 2 attempts): load the user's message and the answer by id (owner-scoped), check `memory_enabled` and the `MEMORY_WRITES_DISABLED` flag (skip without a model call), search the 20 most similar memories, call the model with usage collector and trace `job.extract_memories`, apply ops, record spend (model and embeddings) with `requests=0`. Idempotent: a re-run of the same job finds its create ops as duplicates (>= 0.9) and its update/delete already applied.
7. **Enqueue** in `RunService._write_outcome` for completed `send` and `resume` runs (not regenerate: the user message was already read): only when the user has `memory_enabled`; key `mem:{assistant_message_id}`. Needs `users.get_by_id` in the same transaction.
8. **Fake LLM** (`llm/fake.py`): role `memory_extract` + `with_structured_output` returns `MemoryOps` from the user message: a `[[fixture:id]]` fixture's optional `memory_ops`, else a plain "remember (that) X" rule, else no ops. So e2e and tests run offline.
9. **Tracing/config**: `RunName` gains `job.extract_memories`; pricing/factory unchanged (role exists).
10. **Evals**: `evals/datasets/memory.extract.jsonl` (25 AI-drafted records, 6 smoke, sensitive and instruction-like cases included), record model `MemoryExtractCase` in `backend/evals/datasets.py`, step `backend/evals/memory.py` (real `plan_ops` with the real model; no DB), metrics `memory.extract_precision`, `memory.extract_recall`, `memory.sensitive_refusal`, wired into `run.py` (`METRIC_KEYS`, dataset registry, smoke subset). Offline check in `LLM_MODE=fake`.
11. **Tests**: policy unit tests (every category, near-misses that must pass, instruction patterns, unicode and case tricks); extraction unit tests with a scripted model (cap of 3, unknown id dropped, filters applied, malformed output); integration tests against real Postgres (store CRUD, isolation, dedupe 0.9, cap 200 / LRU, job end to end via a real send and worker incl. memory disabled and flag disabled, idempotent re-run, spend recorded); mutation checks (remove the sensitive filter, remove the user namespace).
12. **Docs/gates**: `memory/ai.md`, `memory/_index.md`, `backend/AGENTS.md` (structure), doc 08 §7/§9 small corrections, doc 09 dataset line, `tests/coverage-baseline.json` (`backend/app/memory`), `tasks/deferred.md`, `CHANGELOG.md`; move task files to `tasks/done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/app/memory/__init__.py`, `store.py`, `policy.py`, `extract.py` | create | store, filters, extraction |
| `backend/prompts/nexus.memory_extract.md` | create | v1 |
| `backend/app/jobs/handlers.py` | modify | `extract_memories` + registry |
| `backend/app/services/runs.py` | modify | enqueue after a completed send/resume |
| `backend/app/migrate.py` | modify | store with index |
| `backend/app/llm/fake.py`, `fixtures/memory_*.json` | modify/create | scripted extraction |
| `backend/app/llm/tracing.py` | modify | `RunName` |
| `backend/evals/datasets.py`, `evals/memory.py`, `evals/run.py` | modify/create | suite and metrics |
| `evals/datasets/memory.extract.jsonl` | create | 25 records |
| `backend/tests/memory/*`, `tests/jobs/test_extract_memories.py`, `tests/evals/test_memory_step.py` | create | tests |
| `tests/coverage-baseline.json`, docs, memory, CHANGELOG, `tasks/deferred.md` | modify | |

## AI Impact & Eval Plan

- Prompt `nexus.memory_extract` v1, role `memory_extract`, new job; no graph/tool change.
- `make eval` / `eval-smoke` calls the real model and costs credits; as in TASK-022/023 it is **not** run without approval. Offline: the whole pipeline in `LLM_MODE=fake`. The absolute gate `memory.sensitive_refusal` therefore stays unmeasured on a real model (deferred, with the baselines). The code-level filter is what makes the gate achievable: a record fails only if the model's output passes the filter.
- New dataset: `memory.extract` (25 records).
- Tracing: job trace carries the hashed user id, conversation id and prompt version; inputs/outputs go through the existing masking. Memory text and the user's message are PII-like: not logged by the app, masked in traces.

## Uncertainties

- `AsyncPostgresStore` with a custom async embed function and cosine distance: check the returned `score` is a similarity (1 = identical) and that `setup()` creates the `vector` extension (verify on the Compose Postgres).
- How `with_structured_output(..., strict=True)` behaves through `with_fallbacks`/the factory for role `memory_extract` (no fallback configured, so it is a plain `ChatOpenAI`); cannot be verified live without a paid call, so only the call shape is tested.
- Whether `AsyncPostgresStore` read-your-write holds within one job (batching background task): tests will read after write.
- Fake embeddings are a hashed bag of words: the 0.9 threshold is testable with identical and near-identical text only.

## What Will Be Skipped

- The second, model-based "classifier" post-filter of doc 08 §7: the deterministic filter plus the prompt is what is built; a model classifier only if the real eval shows leaks (deferred).
- Reading memories (TASK-026), API/UI (TASK-027), `manage_memory` and slash commands (M7), account-deletion purge (TASK-028).
- Real-model runs and the baselines (credits, review).
- "Least recently used" is by `last_used_at`, which this task sets at write time; TASK-026 updates it when a memory is read into a prompt.

## Risks

- A regex deny-list has false positives (a user's own "I'm a Christian developer" is dropped on purpose) and false negatives (paraphrases); the prompt is the first line, evals measure the rest.
- An extraction bug could write junk for every user: mitigated by `MEMORY_WRITES_DISABLED`, the 3-op cap and the 200 cap.
- Every completed turn now costs one extra background model call: bounded by the daily budgets, which count job spend.

## Ready to implement?

Yes.
