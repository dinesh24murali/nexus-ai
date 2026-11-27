# Task: 039-memory-by-chat

**Status:** done (offline; the real-model items are deferred as D29)
**Created:** 2026-11-22
**ADR refs:** ADR-0010, ADR-0006

---

## Goal

Let the user list, save and forget memories by chat: the `manage_memory` pipeline (templates and deterministic store operations, no chat model), with a confirmation card before every forget.

## Background

Plan v1.3, TASK-039; doc 08 §2.5 (`manage_memory` row), §2.4 (`forget_memory` interrupt), §7; doc 09 GAP-07/08/09. Depends on TASK-038 (router, slash commands) and TASK-026 (memory read side). Memories are still extracted automatically by the job; this adds the explicit path.

## Acceptance Criteria

- [x] `memory.e2e` cases pass through `manage_memory`, including the one-clear-match and ambiguous forget cases (doc 09 §9) — **built and run offline (scripted models, real Postgres store: `tests/evals/test_memory_e2e.py`); the real-model run is deferred (D29)**
- [x] Every forget interrupts before deleting (`tools.approval_enforced`: three no-model probes + mutation checks); reject, an unoffered id, a flipped kill switch and expiry delete nothing

## Dependencies

- **Tasks:** 038, 026, 025, 027
- **Memory files:** `memory/ai.md`, `memory/api.md`
- **Docs:** doc 08 §2.4/§2.5/§7, doc 09 §9, doc 10 (`forget_memory`)

## AI Impact

- **Prompts touched:** none (no model call in `manage_memory`)
- **Graph / tools / retrieval changes:** `manage_memory` and `forget_confirm` nodes; `forget_memory` interrupt; `memory_updated` event
- **Eval datasets affected / added:** `memory.e2e` (new, 8 / 2)
- **Threat model entries:** LLM-T17 (a forget always asks), memory rows of doc 10
- **Expected cost / latency delta:** none for the model; one query embedding for `forget` and `save` (billed to the run)

## Files Expected to Change

See `039-memory-by-chat.scratch.md`.
