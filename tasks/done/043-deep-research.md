# Task: 043-deep-research

**Status:** done (offline; the real-model items are deferred as D35)
**Created:** 2026-11-22
**ADR refs:** ADR-0010 (router; `deep_research` is the last intent to get its pipeline), ADR-0011 (PDF rendering; research PDFs go through the same job), ADR-0006 (runs are server-side; a pause holds no lease)

---

## Goal

Answer a research question with a cited report: plan the searches, let the user approve (or edit) the plan once, run exactly those searches in parallel, write the report into the chat, and turn it into a PDF when one was asked for.

## Background

Plan v1.3, TASK-043 (M7, `[012-documents-research]`); doc 08 sections 2.2, 2.3, 2.4, 2.5, 4 (prompts), 9; doc 09 (`research.golden`, rt-25, `research.unapproved_queries`, `ops.cost_per_research_run_usd`); doc 10 LLM-T20. The router, `/research`, the `research_plan` interrupt type, the `progress` event, `edited_args.queries` and the `research_report` document kind already exist in the spec (1.4.1); until now `/research` answered "not available yet" and the router fell back to chat. Documents (TASK-041/042) are built.

## Acceptance Criteria

- [x] `deep_research` pipeline: plan (`nexus.research_plan`, 3 to 6 queries, max output 1k) -> `interrupt(research_plan)` (one approval for the whole plan; approve, edit the queries, reject) -> approved queries searched in parallel (5 results each, total <= 16k tokens, `progress` events) -> report (`nexus.research_report` on `long_form`, max output 4k, `[n]` citations, streamed into the chat) -> a PDF through the `document_created` path when `output=pdf`
- [x] **No unapproved query ever reaches the search provider** (`research.unapproved_queries = 0`): the search node reads only what the user approved; a rejected plan runs nothing; an edit is validated (1 to 6 queries, each 1 to 400 characters)
- [x] The planner sees the topic and the date only: no memories, no thread text (LLM-T20); the plan is shown to the user before any search
- [x] Worst-case cost (about $0.043) is checked before the run against the user's remaining budget and the global cap; the daily document limit is checked first when a PDF is asked for; a refusal is a chat message and nothing runs
- [x] Cancel semantics (doc 08 section 2.3): cancelled before the report, no report is written and the message lists the searches done; once the PDF job is queued the document finishes
- [x] `research_plan` approval card in the UI (edit, add, remove, reject), `progress` shown while it runs, the report with citations and the PDF card; `/research` and the router reach it (help text, availability, kill switch `INTENTS_DISABLED=deep_research`)
- [x] `research.unapproved_queries = 0` and `tools.approval_enforced = 1` (absolute gates, deterministic, recorded); rt-25 passes offline (an obeying planner that leaks a canary is rejected by the eval's auto-approver and no search carries it); `research.golden` dataset (10 / 2) with recorded-style Tavily fixtures built and run offline
- [-] `research.golden` reviewed, a real-model run (rt-25 on the real models, real Tavily recordings), `ops.cost_per_research_run_usd` baseline <= $0.05 — needs a paid run: deferred (D35)

## Dependencies

- **Tasks:** 041 (done), 042 (done), 023 (web search approval, done), 038 (router, done)
- **Memory files:** `memory/ai.md`, `memory/api.md`, `backend/AGENTS.md`, `frontend/AGENTS.md`, `evals/AGENTS.md`
- **Docs:** doc 08 sections 2.2 to 2.6, 4, 9; doc 09 sections 3, 5, 9; doc 10 LLM-T20, LLM-T02 (results are data), LLM-T07 (no personal data in queries)

## AI Impact

- **Prompts touched:** `nexus.research_plan` v1 (new), `nexus.research_report` v1 (new); `nexus.system` unchanged (its "never offer research yourself" rule is already there)
- **Models / params changed:** new role `research_plan` (the chat model, effort `low`, max output 1000, 60 s); `long_form` is used for the report (streamed)
- **Graph / tools / retrieval changes:** new pipeline `deep_research` (nodes `research_check`, `research_plan`, `research_confirm`, `research_search`, `research_report`, `research_document`); a second `interrupt` type (`research_plan`); `progress` events; the report node streams; no new model-callable tool (the searches are made by the pipeline, one approval for the plan)
- **Eval datasets affected / added:** `evals/datasets/research.golden.jsonl` (new), `evals/fixtures/web/` (new)
- **Threat model entries:** LLM-T20 (built), rows for `research_plan` and `research_search` (doc 10 sections 3 and 7)
- **Expected cost / latency delta:** none for chat; a research run costs <= $0.043 worst case

## Files Expected to Change

See `043-deep-research.scratch.md`.

## Notes

- Web results are untrusted data: they reach the report prompt only as escaped `<web_result>` blocks, the report cites them as `[n]`, a link in the PDF is `http(s)` only and printed in full (the TASK-041 renderer).
