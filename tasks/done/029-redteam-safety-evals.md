# Task: 029-redteam-safety-evals

**Status:** done
**Created:** 2026-11-22
**ADR refs:** ADR-0009 (gate tiers: absolute gates recorded at their target), ADR-0008 (prompts)

---

## Goal

The `safety.redteam` set covers the attacks doc 09 section 8 lists for v1 (injection through documents, web results and memories, exfiltration, cross-tenant probing, tool abuse) and scores them with deterministic canaries, so `safety.injection_resistance` and `safety.cross_tenant_leaks` exist as absolute gates.

## Background

TASK-029 in `docs/02_outputs/05_implementation_plan.md` (M6); doc 09 section 8 (the rt-xx table), doc 10 (threat IDs). Today the dataset has the 10 chat-only records (rt-06, 07, 17, 19, 21) from the eval harness task; the records that need a setup (a poisoned document, a poisoned web result, a planted memory, another user) and the tool canaries do not exist. `tasks/deferred.md` D13 and D17 say the red-team records for the new tools and for rt-05 / rt-08 arrive here.

## Acceptance Criteria

- [-] Injection resistance >= 0.95 (absolute gate): the metric exists, is computed over the injection records (rt-01-05, 08, 09, 13, 20), and the gate is recorded in `evals/eval-baseline.json` at its target — **built and recorded; the value was never measured on a real model (D19)**
- [-] Cross-user leakage = 0: `safety.cross_tenant_leaks` (a count; rt-10, 11, 12, 21) exists and its absolute gate is recorded at 0 — **same: built and recorded, not measured on a real model (D19)**
- [x] 40 records, every one citing a threat id, each with deterministic canaries (token in the answer or in tool results, forbidden or unapproved tool call, tool-call cap, forbidden memory write); about 10 tagged smoke
- [x] The harness is tested against a model that obeys every attack (every canary fires) and a model that resists (none fires), so a gate cannot pass because the check is blind
- [-] Failures found by the suite are fixed in the prompt or code — **no real-model run, so no failures were found; deferred with D19**

## Dependencies

- **Tasks:** TASK-015, TASK-023, TASK-026
- **Memory files:** `memory/ai.md`, `memory/api.md`
- **Docs:** doc 09 sections 8-9, doc 10, `evals/AGENTS.md`

## AI Impact

- **Prompts touched:** none (a failing real-model record would change `nexus.system`; not measured yet)
- **Models / params changed:** none
- **Graph / tools / retrieval changes:** none
- **Eval datasets affected / added:** `evals/datasets/safety.redteam.jsonl` (10 -> ~40 records), new step in `backend/evals/redteam.py`, new metrics `safety.injection_resistance`, `safety.cross_tenant_leaks`
- **Threat model entries:** LLM-T01, T02, T04, T05, T06, T07, T08, T15 (records now exist); LLM-T17, T18, T20 stay with M7
- **Expected cost / latency delta:** one real-model turn per record (+ one extraction call for rt-05 / rt-09); smoke budget < $0.50

## Files Expected to Change

See `029-redteam-safety-evals.scratch.md`.

## Notes

- A paid real-model run was not authorised in this session (same as D10/D15/D17): the suite is built and tested offline; the first real measurement and any fixes it drives are deferred (D19).

## Outcome

Built and tested offline: 40 records, the tool-backed step, two metrics, two absolute gates recorded at their targets. Not done (deferred): the real-model run, the developer review of the 30 new records and the fixes the run drives (D19); rt-22/23/25 (D20, M7).
