# Scratch: 003b-plan-revisions-v1.1

> Decisions Q1–Q18 are listed in `003b-plan-revisions-v1.1.md`. The developer confirmed them in the grilling session on 2026-11-21 and asked to apply them; this file records how.

---

## Plan

1. **Doc 08 AI design** (largest change): state schema (no `read_untrusted_this_turn`, `RouteDecision` with 4 intents, no schedule slots, `source` adds `last_answer`), top-level graph (command parse → router), `general_chat` (TOOL_POLICY = web_search only, GAP-03/04 folded in), §2.5 subgraph table (no smalltalk/schedule; manage_memory with `forget_memory` interrupt; documents `last_answer`; token limits), model table (`router`, `long_form`), prompt catalog (no `nexus.smalltalk`; `nexus.system` rules from GAP-05/06 + Q1/Q16), tool catalog (no `remember_fact`), RAG (GAP-01), memory (GAP-07/08 as amended), streaming (`source=command`), jobs (heartbeat; no schedule jobs), observability (eval tracing), failure modes, delete §13.
2. **Doc 01 PRD:** v1.1 scope, US-14/22/23/24, FR-030/035, FR-060…066, G10, M7, backlog (schedules).
3. **Doc 02 functional spec:** UF-006, UF-009…012, screens, FS-005/006/009…012, state diagrams, notifications, permissions, retention.
4. **Doc 03 architecture:** worker jobs, module layout, ERD/tables (no schedules, no `kind/unread`, add `runtime_flags`, `jobs.heartbeat`), indexes, observability, ADR table.
5. **Doc 04 API spec:** remove `/schedules` paths + schemas + `ScheduleId`; `SendMessageRequest` without `mode`/`client_timezone`; `PendingInterrupt`/`ResumeRunRequest`; `RouteSelectedEvent` (`command`, 4 intents); `DocumentObject.kind` + `last_answer`; validate.
6. **Doc 05 implementation plan:** TASK-012 heartbeat, TASK-019 GAP-01/04, TASK-022 GAP-06, TASK-023 GAP-03, TASK-026 without remember_fact, TASK-028 purge, M7 tasks (038–043, 046), remove 044/045, dependency graph.
7. **Doc 06 dev setup:** eval budgets, CI weekly full eval.
8. **Doc 09 eval plan:** datasets/metrics (router 4 intents, no smalltalk), approval metric, CI table, red-team rows, §9 gap coverage rewritten as standing coverage.
9. **Doc 10 threat model:** T02/T04/T08/T17/T20, remove T19, permission matrix, kill switches → `runtime_flags`, incident response.
10. **ADRs** 0002, 0004, 0007, 0009, 0010: revision notes and content.
11. **Gate + memory + env:** `evals/eval-baseline.json`, `memory/ai.md`, `memory/project.md`, `.env.example`; intake mention of `remember_fact` (mark revised).
12. Grep for stale terms; validate the OpenAPI spec; CHANGELOG; move task to done. No commit.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| docs/02_outputs/01,02,03,05,06,08,09,10 | modify | per plan |
| docs/02_outputs/04_api_spec.yaml | modify | remove schedules, `mode`; new enums |
| adr/0002, 0004, 0007, 0009, 0010 | modify | revised in place (Q18) |
| evals/eval-baseline.json | modify | targets for removed/renamed metrics |
| memory/ai.md, memory/project.md | modify | |
| .env.example | modify | router/runtime flags |
| docs/00_intake/intake_questionnaire.md | modify | only if it states remember_fact behaviour |
| CHANGELOG.md | modify | |

## AI Impact & Eval Plan

- Paper-only; no suites exist yet, so nothing to run. `metrics` in the baseline stays empty.

## Uncertainties

- Token limits in Q10 (16k results / 4k output) are estimates; re-checked when M7 evals first run.
- Whether `long_form` at effort `medium` fits a 1,500-word report in 4k output tokens incl. reasoning; noted as a tuning item.

## What Will Be Skipped

- No code. `docs/01_prompts/*` untouched. Task files in `tasks/done/` are history and stay as they are.

## Risks

- Cross-doc inconsistencies after many edits → finish with a grep for `schedul`, `smalltalk`, `remember_fact`, `taint`, `read_untrusted`, `client_timezone`, `mode`, `nightly`, `hot-reload`, `GAP-0` and re-validate the spec.
