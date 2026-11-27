# Task: 038-router-dispatch

**Status:** done (offline; the real-model items are deferred as D29)
**Created:** 2026-11-22
**ADR refs:** ADR-0010 (intent router), ADR-0006 (SSE), ADR-0008 (prompts), ADR-0009 (gates)

---

## Goal

Put an intent router in front of the chat agent: a `route` node (`RouteDecision` on `gpt-6-luna`) that runs in parallel with `load_context`, a deterministic slash-command parser that skips it, dispatch to per-intent subgraphs with `general_chat` as the safe fallback, and the `route_selected` event.

## Background

Plan v1.3, TASK-038 (M7, roadmap group `011-intent-router`); design in `docs/02_outputs/08_ai_system_design.md` §2.1, §2.2, §2.6; eval plan §3 (`router.*`), rt-22; threat LLM-T17. TASK-035 (its listed dependency) was deferred by the developer on 2026-11-22 (`tasks/deferred.md` D28). Only `general_chat` and `manage_memory` (TASK-039) exist as pipelines: `generate_document` and `deep_research` come with TASK-041/043, so until then the router may *name* them but dispatch treats them as unavailable (fallback to `general_chat`, source `fallback`); a slash command for them gets a short explanation.

## Acceptance Criteria

- [x] Unit tests (fake LLM): fallback on low confidence, timeout and invalid JSON; slash-command bypass; resume keeps the subgraph (`tests/graph/test_router.py`, `test_dispatch.py`, `tests/services/test_runs_routing.py`)
- [x] Parser unit tests: every command in doc 08 §2.6, case-insensitivity, commands only at the start, malformed commands → usage reply (`tests/graph/test_commands.py`)
- [-] `min_confidence` tuned on the first reviewed full run, or dropped if degenerate; result in `memory/ai.md` (needs a paid run: deferred, D29)
- [-] `ops.ttft_p95_ms` measured end to end with the router; keyword pre-gate if it misses (needs a paid run: deferred, D29)
- [-] `router.intents` dataset (120 / 24, 4 intents) **built and checked offline**; developer review and first baselines for `router.intent_accuracy`, `router.macro_f1`, `router.slot_accuracy`, `router.latency_p95_ms` need a paid run (deferred, D29)
- [x] rt-22a/b records exist and pass offline against a resisting router and fail against an obeying one; router input asserted to contain user text only (unit test with a poisoned tool message, and the eval test); the real-model run is D29

## Dependencies

- **Tasks:** 026 (memory in context), 023 (approvals). TASK-035 waived (D28)
- **Memory files:** `memory/ai.md`, `memory/api.md`
- **Docs:** doc 08 §2, doc 09 §3/§8, doc 10 LLM-T17, ADR-0010

## AI Impact

- **Prompts touched:** new `nexus.route` v1 (front-matter `min_confidence: 0.70`). `nexus.system` stays v4 (the "never offer a PDF or research yourself" rule is already in it)
- **Models / params changed:** `router` role already exists in `llm/factory.py` (effort none, 150 max output, 5 s, no retries)
- **Graph / tools / retrieval changes:** new `route` node, `dispatch`, parallel start; `ChatState.route`
- **Eval datasets affected / added:** `router.intents` (new), `safety.redteam` (+ rt-22)
- **Threat model entries:** LLM-T17
- **Expected cost / latency delta:** +1 luna call per message (~150-300 ms, ~$0.00009), partly hidden by running parallel with `load_context`

## Files Expected to Change

See `038-router-dispatch.scratch.md`.
