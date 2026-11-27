# Task: 026-memory-in-context

**Status:** done
**Created:** 2026-11-22
**ADR refs:** ADR-0003 (memories in Postgres), ADR-0009 (gate tiers)

---

## Goal

Nexus uses what it remembers: at the start of every run `load_context` fetches the user's relevant memories and the late context message shows them to the model as untrusted user-profile data.

## Background

TASK-026 in `docs/02_outputs/05_implementation_plan.md` (M5). Design: doc 08 §7 "Read" (top 8 relevant + up to 5 most recent `preference` memories, deduped, <= 600 tokens, in the late context message, skipped when `memory_enabled=false`) and §4 (cache-friendly layout). TASK-025 built the write side; this task adds the read side and refreshes `last_used_at` so the 200 cap evicts the least recently *used*. Threats: LLM-T04 (memory poisoning), LLM-T05/T06 (cross-user leakage). API and UI are TASK-027.

## Acceptance Criteria

- [x] "Remember that I prefer Go" leads to exactly one Go memory after the extraction job, and the next turn's request shows it in `<user_memory>` (`memory.e2e`)
- [x] rt-05: a web result asking to remember something never produces a memory (no memory-writing tool is bound; extraction reads only the user's message)
- [x] Isolation test passes: user B's run never sees user A's memories, even when B's message is A's memory text
- [x] Memory off (user setting) = nothing read, no embedding call
- [x] Memories are escaped data: a memory cannot close `<user_memory>` or open another tag
- [x] `last_used_at` of the memories shown is refreshed, without re-embedding and without bringing back a memory the user just deleted
- [x] A failed memory lookup never fails the run; the query embedding is billed to the run
- [x] Prompts `nexus.context` v2 and `nexus.system` v4 written and tested offline
- [-] Real-model smoke run of the new prompt versions and the real-model rt-05 record: skipped, deferred (`tasks/deferred.md` D17, D13); needs paid runs

## Dependencies

- **Tasks:** TASK-025, TASK-023
- **Memory files:** `memory/ai.md` (Long-term Memory, graph), `memory/api.md`
- **Docs:** doc 08 §4 and §7, doc 10 (LLM-T04), doc 09 §8 (rt-05)

## AI Impact

- **Prompts touched:** `nexus.context` v1 -> v2 (one line about `<user_memory>`), `nexus.system` v3 -> v4 (Memory section: how to use remembered facts)
- **Models / params changed:** none (query embedding with `text-embedding-3-small`, billed to the run)
- **Graph / tools / retrieval changes:** `load_context` gets a memory lookup; no new tool, no new node
- **Eval datasets affected / added:** none run (needs credits); rt-05 as a deterministic test, the real-model record rides with TASK-029
- **Threat model entries:** LLM-T04, LLM-T06 (mitigation text updated)
- **Expected cost / latency delta:** one embedding call (a few tokens) plus one Postgres search per run when memory is on; late context message grows by <= 600 tokens (after the cached prefix, so no cache loss)

## Files Expected to Change

See `026-memory-in-context.scratch.md`.

## Notes

- Memory text is untrusted user data: never logged, escaped, only inside `<user_memory>`.
