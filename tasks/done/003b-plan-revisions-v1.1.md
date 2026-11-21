# Task: 003b-plan-revisions-v1.1

**Status:** done
**Created:** 2026-11-21
**ADR refs:** ADR-0002, ADR-0004, ADR-0005, ADR-0007, ADR-0009, ADR-0010 (revised in place)

---

## Goal

Apply the decisions from the 2026-11-21 grilling session (Q1–Q18) on the v1.1 intent-router scope and the doc 08 §13 design gaps, so the plan matches what will be built.

## Background

After task 003, two doc-only revisions landed without a task file: the design-gap review (doc 08 §13 GAP-01…09) and the v1.1 intent-router scope (ADR-0010, M7). A second grilling session stress-tested both and settled 18 decisions. The id `003b` keeps the roadmap task numbers (004+) stable. Docs only; no code.

## Decisions to Apply

| Q | Decision |
|---|----------|
| Q1 | v1.1 = router + `general_chat`, `manage_memory`, `generate_document`, `deep_research`. **Schedules** move to the PRD backlog, to return together with an out-of-app delivery channel (email/push). **`smalltalk_help` is cut**; a static capability paragraph in `nexus.system` and `/help` replace it |
| Q2 | LLM router on every message; G3 and `ops.ttft_p95_ms` are measured end to end including the router; a keyword pre-gate is the documented fallback if TTFT misses |
| Q3 | GAP-01…09 closed: recent ready files in the late context message + `file_id` in `<document>` tags (no new tool); GAP-02 deferred until RAG evals; interrupt first, then run all of the message's calls; the cap counts individual calls; scope and routing rules added to `nexus.system`; dedupe and `forget` rules (GAP-07/08/09 as amended by Q11/Q12) |
| Q4 | Job heartbeat: the worker refreshes `jobs.locked_at` every 30 s; a job is stale after 2 min without a heartbeat; each job kind has its own hard timeout; a test proves no job runs twice |
| Q5 | Kill switches live in a Postgres `runtime_flags` table set with `app.cli flags set`, read with a 60 s cache; env gives boot defaults |
| Q6 | Full eval weekly + before releases and model changes (no nightly); smoke on PR/merge; eval runs send one trace per case (no per-node spans); CI Langfuse project in its own organisation; budgets smoke < $0.75, full < $7 |
| Q7 | Keep the router's self-reported confidence and tune the threshold on eval data (option b) |
| Q8 | Slash commands are parsed deterministically by the backend from the start of `content`; `mode` is removed from `SendMessageRequest`; `route_selected` gets `source=command` |
| Q9 | New document source `last_answer`: the previous assistant message becomes a `DocumentDraft` with no LLM call; research with `output: pdf` streams the report in chat, then renders it this way |
| Q10 | Cost caps by construction: token limits set so each run's worst case ≤ $0.05 (research ≈ $0.043, documents ≈ $0.037); pre-check against the user budget and the global cap; no mid-run cut-off |
| Q11 | `remember_fact` removed from v1, with the taint rule, FR-035 and the conditional `TOOL_POLICY`; `manage_memory` (v1.1) is the only explicit chat path for memory; extraction is skipped on `manage_memory` turns |
| Q12 | Every forget goes through an approval card (`forget_memory`); saves need no approval |
| Q13 | Regenerate only on `general_chat` answers; cancel works everywhere; a queued render job finishes |
| Q14 | TASK-041 adds documents (rows + PDFs) to `purge_user` and the cross-store purge test |
| Q15 | `min_confidence` lives in the `nexus.route.md` front-matter, starts at 0.70, tuned on the first reviewed full `router.intents` run for the best `router.intent_accuracy` (ties → higher), re-tuned on router prompt/model changes; if confidence is degenerate (> 90% of cases ≥ 0.9, or wrong routes don't score lower on average), drop the threshold and fall back on errors only |
| Q16 | Router input stays user-authored only (last 3 user messages + previous intent); `nexus.system` tells the chat model never to offer a PDF or research itself and to name the phrasing or slash command instead |
| Q17 | Slash grammar: `/pdf`, `/pdf last`, `/brief <topic>`, `/research <question>`, `/memory`, `/memory remember <text>`, `/memory forget <text>`, `/help` (static text, no model call); anything else after a known command gets a usage reply |
| Q18 | Record as task 003b; revise ADRs 0002, 0004, 0007, 0009, 0010 in place with revision notes; `runtime_flags` documented in the architecture and threat model (no new ADR); developer reviews the diff; no commit until asked |

## Acceptance Criteria

- [x] Docs 01–10 reflect every decision above; no remaining v1.1 references to schedules (outside the backlog), `smalltalk_help`, `remember_fact`, the taint rule, `mode`/`client_timezone` on the request, nightly full evals, or env hot-reloaded kill switches
- [x] Doc 08 §13 gap rows are deleted, each resolution folded into its section
- [x] `04_api_spec.yaml` parses and every `$ref` resolves, with no unused schemas or parameters (full OpenAPI validation with Redocly wasn't run: downloading it was blocked in this session)
- [x] ADRs 0002, 0004, 0007, 0009, 0010 carry a `Revised: 2026-11-21 (task 003b)` note (also 0005, for the weekly drift alarm)
- [x] `memory/ai.md`, `memory/project.md`, `evals/eval-baseline.json`, `.env.example` updated
- [x] `CHANGELOG.md` entry; task moved to `tasks/done/`; nothing committed

## Dependencies

- **Tasks:** 003-plan-revisions
- **Memory files:** `memory/ai.md`, `memory/project.md`, `memory/api.md`
- **Docs:** `docs/02_outputs/01`–`10`, `adr/0002`, `0004`, `0007`, `0009`, `0010`

## AI Impact

- **Prompts touched:** on paper — `nexus.system` (capability paragraph, scope rule, routing rules, no-offer rule), `nexus.context` (recent files), `nexus.route` (`min_confidence` front-matter, 4 intents); `nexus.smalltalk` removed
- **Models / params changed:** `router` no longer serves smalltalk; research and document token limits lowered for the $0.05 worst-case cap
- **Graph / tools / retrieval changes:** `remember_fact` removed; `TOOL_POLICY` = `web_search` always; new interrupt `forget_memory`; `last_answer` document source; deterministic slash commands bypass the router
- **Eval datasets affected / added:** `router.intents` (4 intents); `tools.routing` (no `remember_fact` cases); `memory.e2e` via `manage_memory`; red-team rt-05 reworded, rt-24/rt-26 removed
- **Threat model entries:** LLM-T02, T04, T08, T17, T19 (removed), T20
- **Expected cost / latency delta:** research and document runs ≤ $0.05 worst case; eval spend roughly a quarter of nightly

## Notes

- ADRs are `Accepted` and `adr/AGENTS.md` forbids editing them; the developer decided (Q18) to revise in place, as in task 003, because no code depends on them yet. Originals are in commit `8be9349`.
- Two small additions beyond the decision list, both following from it: the `answer` document kind (for `last_answer` PDFs of non-research answers) and `409 REGENERATE_NOT_SUPPORTED` on regenerate for non-chat answers. ADR-0005 got a one-line revision (weekly drift alarm).
