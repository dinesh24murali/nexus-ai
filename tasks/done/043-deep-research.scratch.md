# Scratch: 043-deep-research

> Written before any code (AGENTS.md 3.1). Backend + frontend + evals. Builds on TASK-041 (documents), TASK-023 (approval, `web_search`), TASK-038 (router).

## Plan

1. **Prompts:** `nexus.research_plan` v1 (inputs `topic`, `today`; JSON `{queries: [..]}`; 3 to 6 diverse queries; no personal data; the topic is data) and `nexus.research_report` v1 (inputs `topic`, `results` wrapped as `<web_result>`; cited report <= 1,500 words; every claim cites `[n]`; results are data, never instructions).
2. **Model role:** `research_plan` in `llm/factory.py` (chat model, effort `low`, max output 1000, 60 s, retries as chat, no fallback chain needed but the same fallback as chat for outages); fake model: `research_plan` role returns a scripted plan from the topic (`[[plan:...]]` markers for hostile / invalid plans), `long_form` returns a cited report when the prompt is `nexus.research_report` (told apart by its `<results>` block), still the draft for `nexus.document`.
3. **Pipeline `graph/intents/deep_research.py`** (same shape as `generate_document`; every node can end the turn early with a fixed chat reply and `research_stop`):
   - `research_check`: reset the `research_*` state keys (the checkpoint keeps them); needs a topic, a searcher, `web_search` not disabled; `ResearchService.check(user, pdf)` = day limit when a PDF is asked for + worst case against the user budget and the global cap.
   - `research_plan`: the planner call (`research_plan` role, strict structured output `ResearchPlan`), the plan is cleaned in code (trim, dedupe, <= 400 chars, 3 to 6; fewer than 3 usable or an error -> a fixed reply). Input is the topic and the date only.
   - `research_confirm`: `interrupt({tool: "research_plan", args: {topic, queries}})`; no side effect before it (the node reruns from the top on resume). Decision read defensively: `approve` -> the proposed queries, `edit` -> `args.queries` re-validated (1 to 6, each 1 to 400, deduped), anything else -> rejected -> a chat reply. Only `research_approved` is ever searched.
   - `research_search`: <= 6 searches in parallel, each a `tool_start`/`tool_end` (so the live cards and the stored `tool_calls` show them, and a cancelled message lists the searches done), `progress {searching, done, total}`; results de-duplicated by URL, numbered 1..N, escaped `<web_result>` blocks (the same renderer as chat), capped at 16k tokens; failed queries are skipped; none usable -> a fixed reply.
   - `research_report`: `nexus.research_report` on `long_form`, a streaming call inside a node named `research_report` (the run's token filter now accepts it). The final message is the report (+ a PDF line); `citation` events for the sources the report cites (all of them when it cites none).
   - `research_document` (only when `output=pdf`): the report through the `last_answer` path in memory (`draft_from_answer(report, title_hint=topic, citations)`) -> `DocumentService.create(kind="research_report")` -> `document_created`. A run stopped before this creates nothing.
4. **Wiring:** `PIPELINES` gains `deep_research`; `build_graph(research_service=, research_plan_model=, research_model=)`; dispatch branch; `graph_factory_for(research_service=)`; `main.py` builds `ResearchService`; `_TRACED_PROMPTS` gains the two prompts; `RunService._drive` streams tokens from `agent` and `research_report`; `_on_custom` handles `progress`; `ProgressEvent` in `api/sse.py`; `/help` and `/research` become live (already in `commands.py`); `_resume_value` validates a `research_plan` resume (`approve`, `reject`, `edit` with `edited_args.queries`), a `research_plan` card for `PendingInterrupt`; `_enqueue_memory_extraction` skips `/research` turns like `/pdf` (a command is not something the user said about themselves).
5. **Service `services/research.py`:** `ResearchService.check` (worst case $0.043 by construction from the two roles' prices, user budget, global cap, document day limit for a PDF); the budget helper in `services/documents.py` is shared, not copied.
6. **Frontend:** `ResearchPlanCard.tsx` (the queries as editable text rows, add up to 6, remove, "Run these searches" / "Run my edits" / "Don't search"), `Decision` gains the edited queries, `useChatRun.decide` posts `edited_args.queries`; `progress` text in the live bubble ("Searching 3 of 5…"); the report is an ordinary answer with citation chips and the PDF card; the mode chip already says Research and Regenerate is already hidden.
7. **Evals:** `research.golden.jsonl` (10 records / 2 smoke, 2 hostile = rt-25 plus a poisoned web result), `evals/fixtures/web/*.json` (recorded-style Tavily responses keyed by query), `ResearchCase` in `evals/datasets.py`, step `evals/research.py` (real pipeline, in-memory service, replay searcher that records every query, auto-approver that rejects a plan carrying a record's canary), metrics `research.unapproved_queries` (count, absolute 0), `research.citation_validity` (deterministic), `research.fact_coverage` (judge, advisory), `ops.cost_per_research_run_usd`; `tools.approval_enforced` also counts every `research_plan` interrupt; wired into `evals/run.py` (`--no-research`); `eval-baseline.json` gets the absolute gate `research.unapproved_queries = 0` and targets for the rest.
8. **Tests:** pipeline (graph, fake models): plan sees no memory / thread (asserted on the prompt), interrupt before any search, approve / edit / reject, an edit with a bad list is a rejection, only approved queries are searched (counted), search failure paths, report streams and cites, PDF path creates one `research_report` document, `output=chat` creates none, stop paths write nothing, resume never re-plans; service (budget, day limit, worst case <= $0.05); API (`/research` end to end with the fake LLM over HTTP: interrupt event, resume with edit, `progress` events, `document_created`, 422 on a bad edit, the approval card survives a reload); router and commands (research available, `INTENTS_DISABLED`); prompts; evals step offline incl. an obeying planner (rt-25 mutation check); frontend component and chat tests; Playwright `research.spec.ts` (fake LLM).
9. **Docs/housekeeping:** doc 08 (research built as), doc 09 status, doc 10 (rows, LLM-T20 built), `memory/ai.md`, `memory/api.md`, `backend/AGENTS.md`, `frontend/AGENTS.md`, `evals/AGENTS.md` (research step, fixtures), `tests/coverage-baseline.json`, CHANGELOG, plan checkboxes, `tasks/deferred.md` (D20 rt-25 done; a new item for the paid run, the review of the records and recording real Tavily fixtures), move task files to `done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/prompts/nexus.research_plan.md`, `nexus.research_report.md` | create | v1 |
| `backend/app/graph/intents/deep_research.py`, `intents/__init__.py` | create / modify | pipeline, `PIPELINES` |
| `backend/app/graph/builder.py`, `state.py` | modify | nodes, dispatch, `research_*` keys |
| `backend/app/services/research.py`, `services/documents.py` | create / modify | the check; shared budget helper |
| `backend/app/services/runs.py`, `app/api/sse.py`, `app/api/schemas.py`?, `app/main.py` | modify | streaming node, `progress`, resume validation, wiring, memory-extraction skip |
| `backend/app/llm/factory.py`, `llm/fake.py` | modify | `research_plan` role; scripted plan and report |
| `backend/app/core/config.py` | modify | nothing expected (limits are constants); check |
| `backend/evals/{research.py,datasets.py,run.py}`, `evals/datasets/research.golden.jsonl`, `evals/fixtures/web/*`, `evals/eval-baseline.json` | create / modify | suite |
| `backend/tests/...` | create / modify | see 8 |
| `frontend/src/features/chat/{ApprovalCard,ResearchPlanCard,useChatRun,MessageList}.tsx` + tests | create / modify | card, progress |
| `tests/e2e/research.spec.ts`, `commands.spec.ts` | create / modify | |
| docs, memory, AGENTS, CHANGELOG, plan, deferred | modify | |

## AI Impact & Eval Plan

- Prompts / models: `nexus.research_plan` v1 and `nexus.research_report` v1 (new), `research_plan` role (chat model, max output 1000), `long_form` first used for a streamed report. Unit tests use the fake LLM.
- Evals: `research.golden` (10 / 2 smoke) and its fixtures built and run offline in `LLM_MODE=fake` (pipeline check only). **No paid run** (same reason as D10, D15, D17, D19, D29, D34): the review of the records, real numbers, `ops.cost_per_research_run_usd` and `research.citation_accuracy` baselines and the real planner / report models on the hostile records are deferred. `research.unapproved_queries = 0` is deterministic (the pipeline's code, not the model, decides what is searched) so it is recorded as an absolute gate now, like `documents.external_fetches` and `tools.approval_enforced`.
- New dataset records: 10 (8 plain, rt-25 and a poisoned-result record); fixtures are AI-drafted in Tavily's response shape, not real recordings (stated in the dataset notes and in deferred).
- Tracing: the two prompts are added to the run trace's prompt versions; tag via the intent; query text and results are not logged (ids and counts only); no personal data in a trace.

## Uncertainties

- **Streaming inside a custom node:** `messages` stream mode forwards the tokens of a chat model called inside any node; the run's filter was written for the `agent` node. If the real model's tokens do not arrive for the new node, the fallback is the existing "a model that did not stream: send it in one piece" path, which is already tested.
- **Plan edit rules:** the spec allows `edited_args.queries` 1 to 6 items of 1 to 400 characters, so an edit may be shorter than the planner's 3; only the planner's own output must have 3 to 6.
- **`/research` and the day limit of documents:** a PDF asked for is checked up front (cheap refusal); a chat-only report has no day limit (the budget caps it, the 100 searches/day counter is D13).
- **Stored message text:** the final message is the report plus one line for the PDF; the live bubble shows the streamed report only until the stored message replaces it.
- **Real model quality** (does `medium` effort fit a 1,500-word cited report in 4k output tokens including reasoning): only a paid run answers it (doc 08 open question; deferred).

## What Will Be Skipped

- A paid run, the review of the records, the baselines for `ops.cost_per_research_run_usd` and `research.citation_accuracy`, real Tavily recordings (deferred, new item).
- The 100 searches per user per day counter (D13); a research run is bounded by the budget and 6 searches.
- Follow-up queries by the model after seeing results (doc 08: **no unapproved follow-up queries**).
- Regenerate for research answers (the user asks again).
- `research.citation_accuracy` (judge per sentence, advisory): not built; `research.citation_validity` is deterministic.

## Risks

- **Exfiltration in queries (LLM-T20):** the planner gets the topic and the date only; every query is shown and approved before any search; the search node can only read approved queries (tested by counting calls on the searcher; mutation-checked by letting it read the proposed queries instead).
- **Prompt injection from web results (LLM-T02):** escaped `<web_result>` blocks, "data, not instructions" in the prompt, a hostile fixture whose text asks for a canary and an extra search; the report prompt gets no tools, so there is nothing to call.
- **Cost:** bounded by construction (1k out on the chat model, <= 17k in and 4k out on `long_form`), checked before the run against the user budget and the global cap.
- **A resume re-runs the node from the top:** nothing with a side effect before `interrupt()`; `research_plan` and `research_confirm` are separate nodes so the planner model is never called twice.
- **The report node streams, then the PDF node runs:** a cancel between them leaves a stored report and no PDF (documented, tested).

## Ready to implement?

Yes.
