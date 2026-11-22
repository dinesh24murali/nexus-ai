# Scratch: 039-memory-by-chat

> Written before any code (AGENTS.md 3.1). Builds on TASK-038 (router, commands, dispatch).

## Plan

1. **Memory operations** `backend/app/memory/ops.py` (pure parts + store calls): `render_list`, `save_reply`/`forget_reply` templates, `choose_candidates()` (top 3, similarity >= 0.75; one candidate when the best is >= 0.1 above the next, else up to 3), `guess_category()` (deterministic, no model), `third_person()` for texts that start with "I"/"My". Constants `FORGET_MIN_SIMILARITY = 0.75`, `FORGET_CLEAR_MARGIN = 0.1`, `FORGET_MAX_CANDIDATES = 3`.
2. **Manager** `backend/app/memory/manage.py`: a `MemoryManager` protocol (status, list, save, find, forget) and `memory_manager_for(factory, settings, get_embedder)`, the production one (a store per call like `memory_lookup_for`; owner = the run's trusted user id; embedding tokens reported so the run bills them). `save` goes through `memory.policy.check`, the 0.9 duplicate check and the 200 cap by reusing `extract.apply_plan` (gets a `source` argument: `chat`). Never log memory text.
3. **Subgraph** `backend/app/graph/intents/manage_memory.py`: node `manage_memory` (reads `route.slots`; memory off -> notice; `MEMORY_WRITES_DISABLED` -> notice for save/forget; `list` -> template, no model; `save` -> policy + duplicate + `memory_updated`; `forget` -> candidates into state, none -> "not found") and node `forget_confirm` (`interrupt(forget_memory)` with 1-3 candidates; `approve` with a `memory_id` that is one of them deletes exactly that one, anything else is a rejection; confirms with the text). Two nodes, so the search runs once and the interrupt's node re-run on resume does no side effect before the answer. Output is an `AIMessage` in the thread (so the next turn sees it); text arrives in one piece (the run already handles a model that does not stream).
4. **Graph wiring**: `build_graph(memory_manager=...)`; `manage_memory` is only an available pipeline when a manager is given (test and eval graphs without one fall back to `general_chat`).
5. **Run service**: `memory_updated` custom events -> `MemoryUpdatedEvent`; `_resume_value` validates a `forget_memory` decision (`approve` + `edited_args.memory_id` among the pending candidates, required when there are several; `edit` is a 422); `extract_memories` is skipped on these turns (done in 038: `intent == manage_memory`); `graph_factory_for(memory_manager=...)` and the API wiring in `main.py`.
6. **Frontend** (`frontend/src/features/chat/ApprovalCard.tsx`, `useChatRun.ts`, tests): a `forget_memory` card lists the candidates as text (radio group when there are several, the single one preselected), buttons "Forget this memory" / "Keep it"; expired shows "expired" without buttons; decision type gains `memoryId`. Component tests + a Playwright case in `tests/e2e/memory.spec.ts` (`/memory remember`, `/memory`, `/memory forget` with approve and with reject), which needs the stack (`make up && make verify`).
7. **Evals**: `memory.e2e` dataset (8 records, 2 smoke: remember in conversation A / use in B, `/memory remember` exactly one memory and idempotent, list, one-clear-match forget, ambiguous forget, reject keeps everything) + `MemoryE2ECase` + `backend/evals/memory_e2e.py` (real graph, real store on the Compose Postgres under a throwaway user, real embeddings client or the fake one; the step answers the forget card itself) + `memory.e2e_recall` metric wired into `evals/run.py`; `tools.approval_enforced` gets a no-model probe for the forget path (interrupts before deleting; reject deletes nothing). Real-model run = D29.
8. **Docs / memory**: `memory/ai.md`, `memory/api.md`, `frontend/AGENTS.md`, `backend/AGENTS.md`, doc 10 (`forget_memory` row if missing), `CHANGELOG.md`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/app/memory/ops.py`, `manage.py` | create | operations, manager |
| `backend/app/memory/extract.py` | modify | `apply_plan(source=)` |
| `backend/app/graph/intents/manage_memory.py` | create | two nodes |
| `backend/app/graph/builder.py`, `state.py` | modify | wiring, `forget_candidates` |
| `backend/app/services/runs.py`, `backend/app/main.py`, `api/sse.py` | modify | events, resume validation, wiring |
| `backend/evals/{datasets,memory_e2e,tools,run}.py`, `evals/datasets/memory.e2e.jsonl` | create/modify | suite |
| `frontend/src/features/chat/{ApprovalCard,useChatRun}.*`, `tests/e2e/memory.spec.ts` | modify | picker, tests |
| `backend/tests/memory/test_ops.py`, `tests/graph/test_manage_memory.py`, `tests/services/test_runs_forget.py`, `tests/evals/test_memory_e2e.py` | create | tests |
| docs, `CHANGELOG.md`, `tasks/deferred.md` | modify | |

## AI Impact & Eval Plan

- No prompt and no model call in `manage_memory`. The router decides the route (038); the slots (`memory_action`, `memory_content`) come from it or from the parser and are validated, never ids.
- `memory.e2e` (new, 8 / 2). Fake-mode run and tests offline; the real run is D29 (also the first real check that 0.75 similarity finds the memory a user means: question-like text against fact text is often below 0.75, so the constant may need tuning).
- Tracing: no new spans needed; memory text is never put in a log or a trace tag.

## Uncertainties

- 0.75 / 0.1 are the doc's numbers and untuned (D29).
- `list` with memory off shows the notice too (doc: "memory off -> short notice"); managing in Settings still works.
- The stand-in embeddings in fake mode are bags of words, so offline forget cases use wording that overlaps the memory.

## What Will Be Skipped (and why)

- Regenerate-hiding and mode chip in the UI (TASK-040).
- Real-model `memory.e2e` run and gate (D29).
- A model-based category for saved memories (a deterministic guess is used; the extraction job's categories are the reference).

## Risks

- A forget must never delete without a card: two locks (the node interrupts before any delete; the delete takes only a `memory_id` that is one of the node's own candidates) and a test for each, verified by mutation.
- Resume validation must keep an invalid decision pending (422), not consume it.
- Cross-user: every manager call takes the run's user id; a test lets user B ask to forget user A's memory text and finds nothing.

---

## Progress log (written at the end, 2026-11-22)

**Done as planned:** steps 1-8. Verification as in the TASK-038 log, plus 13 manager tests on real Postgres, 3 new Playwright cases (`memory.spec.ts`: remember / list / help / not-available, forget keep + forget, card survives reload and a new message abandons it), `memory.e2e` through the real graph and store (`tests/evals/test_memory_e2e.py`).

**Where the implementation differs from the plan**
- `StoreMemoryManager` (store-backed, no users row) + `memory_manager_for` (adds the DB check) instead of one class; `memory.read.read_for_prompt` was split out of `memory_lookup_for` so the eval can read memories for a throwaway user. `apply_plan` got a `source` argument.
- `TenantMemoryManager` (the red-team stand-in) lives in `evals/tools.py` so the forget probes and `redteam.py` share it.
- `memory.e2e` cases carry a `description` and several expectations (`expect_candidates`, `expect_memories`, `expect_absent`, `expect_count`, `expect_in_replies`, `expect_in_answer`); a case passes only if all of them hold.
- A float bug the tests found: `0.86 - 0.76` is `0.0999...`, so the "clear match" margin is compared with a 1e-9 tolerance.
- The offline `e2e-06` (ambiguous) wording overlaps the memory texts because the fake embeddings are a bag of words; with real embeddings 0.75 may be too strict (D29).

**Mutation checks** (each made a test fail, then reverted): forget deletes without the interrupt; any memory id accepted by `chosen_memory`; the API accepting an id that was not offered; extraction not skipped on `manage_memory` turns; the kill switch ignored on the resume; the stored sentence checked but not the user's own words.
