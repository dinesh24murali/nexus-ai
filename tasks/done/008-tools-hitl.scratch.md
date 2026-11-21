# Scratch: 008-tools-hitl (TASK-022, TASK-023)

> Written before any code, as required by `tasks/AGENTS.md`.

---

## Plan

Everything is test-first (red, then green). Order: bottom-up, so each layer has a working base.

**TASK-022**
1. `calculator.py`: AST whitelist evaluator (numbers, `+ - * / // % **`, unary +/-, parentheses, `sqrt`, `log`, `round`, `abs`). No names, attributes, subscripts, lambdas, comprehensions. Decimal arithmetic (50 digits), bounded power (exponent and result size), node-count and length limits, `x` and unicode operators normalised. Fuzz tests: hand-written attack list + seeded random grammar/mutation fuzzing; each input must either evaluate or raise `CalculatorError`, never anything else, and never touch a sentinel (`__import__`, attribute access, big power in under 1 s).
2. `web_search.py`: args model (query 1..400), `WebSearcher` protocol, `TavilySearcher` (httpx, bearer key, 10 s timeout, `max_results=5`, no raw content, no answer), an offline `FakeWebSearcher` for `LLM_MODE=fake`, results escaped into `<web_result index url domain title>` blocks (<= 800 chars each), http(s) URLs only, web `Citation`s, failure texts that never carry exception text or the key.
3. `policy.py`: `TOOL_POLICY` (`web_search` always, others never); a test asserts every tool the graph can bind has an entry.
4. `tools` node generalised to three tools (same events, same cap, same numbering, per-tool timeouts: 10 s search, 10 s web, 1 s calculator); a call with a recorded decision runs (edit) or is skipped (reject).
5. `nexus.system` v3 + tests (tool routing text, no stale "cannot browse the web").
6. Wiring: `Settings.tavily_api_key`, `factory.build_web_searcher` (fake in fake mode; none without a key, so the tool is simply not offered), `TOOLS_DISABLED` runtime flag removes tools from the graph (graph cache keyed by override + disabled set).

**TASK-023**
7. `approval` node: between `agent` and `tools`; for calls that need approval (and fit under the cap) call `interrupt()` once per call, in call order; decisions go to state (`approvals`); edit = replacement query, reject = "user declined". Nothing runs before the decision (the node precedes `tools`). A reject still yields a ToolMessage per `tool_call_id` (tools node).
8. Run lifecycle: `RunService` detects the interrupt after the stream ends, in ONE transaction releases the lease and stores `pending_interrupt` (new repo function), records spend, emits the `interrupt` event and ends the stream. `start_resume` (new): `_admit`, validate interrupt id / expiry / edit args, take the lease and clear `pending_interrupt` in one transaction (a double click resumes once), `Command(resume=...)`. A resumed run keeps the earlier citations and tool calls of the turn (state), does not count as a new message in `usage_daily`.
9. Abandoning: a new message while an approval is pending drops it (FS-005): the thread is repaired first (every unanswered `tool_call_id` gets an error ToolMessage "not run"), so the next model call is valid. The same repair also runs before a new send in general (a crash or stop between the model call and the tools leaves the same dangling call) and when a run is cancelled.
10. API: `POST /conversations/{id}/runs/resume` (SSE), `ResumeRunRequest` schema, `interrupt` SSE event, `PendingInterrupt` model; spec descriptions updated (1.3.5), contract tests, isolation test.
11. Frontend: `stream.ts` handles `interrupt` (a clean end of the stream), `useChatRun` gets `pending` + `decide()`, `ApprovalCard.tsx` (Approve / Edit / Reject, expired state, exact query shown), the card restored from `GET /conversations/{id}`; tests; Playwright `tools.spec.ts` (approve, edit, reject, reload mid-approval) on the Compose stack with `LLM_MODE=fake`.
12. Evals: `evals/datasets/tools.routing.jsonl` (30 AI-drafted records, doc 09 schema, 10 smoke), record model, runner step `evals/tools.py` (first tool call, args, interrupts, auto-approve for sequences; stub retriever and offline web searcher, no network), metrics `tools.selection_accuracy`, `tools.arg_validity`, `tools.unnecessary_call_rate`, `tools.sequence_accuracy`, `tools.approval_enforced` (deterministic: every web search call interrupted before anything ran, plus a scripted mixed-batch probe that needs no model). Absolute gate `tools.approval_enforced = 1.0` recorded.
13. Docs: spec, threat model (red-team records), `memory/ai.md`, `memory/api.md`, `memory/_index.md`, `tasks/deferred.md`, `CHANGELOG.md`, coverage baseline, task files to `tasks/done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/app/graph/tools/calculator.py` | create | AST evaluator + tool spec |
| `backend/app/graph/tools/web_search.py` | create | args, searcher protocol, Tavily client, fake, rendering |
| `backend/app/graph/policy.py` | create | `TOOL_POLICY`, `needs_approval` |
| `backend/app/graph/nodes/approval.py` | create | interrupt per call needing approval |
| `backend/app/graph/nodes/tools.py` | modify | three tools, decisions |
| `backend/app/graph/nodes/load_context.py`, `state.py` | modify | `approvals`, `tool_log` reset / fields |
| `backend/app/graph/builder.py` | modify | approval node, tools bound, `disabled_tools` |
| `backend/app/graph/tools/search_my_files.py` | modify | `render` method on `Searched` |
| `backend/app/llm/factory.py`, `fake.py`, `fixtures/*.json` | modify / create | web searcher factory, fake fixtures (`web_search`, `web_mixed`, `calc`) |
| `backend/app/core/config.py` | modify | `tavily_api_key`, `web_search_timeout_s` |
| `backend/prompts/nexus.system.md` | modify | v3 |
| `backend/app/api/sse.py`, `schemas.py`, `chat.py` | modify | `interrupt` event, resume request, resume endpoint |
| `backend/app/services/runs.py` | modify | park, resume, repair, graph cache key |
| `backend/app/db/repositories/conversations.py` | modify | `park_run`, `take_pending_and_lease` |
| `docs/02_outputs/04_api_spec.yaml` | modify | descriptions only, 1.3.5 |
| `docs/02_outputs/10_llm_threat_model.md` | modify | red-team records for the new tools |
| `backend/tests/**` | create / modify | see each step |
| `backend/evals/{datasets,tools,run,metrics}.py`, `evals/datasets/tools.routing.jsonl`, `evals/eval-baseline.json` | create / modify | step 12 |
| `frontend/src/api/{stream.ts,schema.ts}`, `features/chat/{useChatRun.ts,ApprovalCard.tsx,ChatView.tsx,...}`, tests | modify / create | step 11 |
| `tests/e2e/tools.spec.ts` | create | step 11 |
| `memory/*.md`, `CHANGELOG.md`, `tasks/deferred.md`, `tests/coverage-baseline.json` | modify | step 13 |

## AI Impact & Eval Plan

- Prompts / models / tools changed: `nexus.system` v3; tools `calculator`, `web_search`; approval node; no model or parameter change.
- Eval datasets: `make eval` / `make eval-smoke` are what AGENTS 3.8 asks for after a prompt or tool change. They call the real model and cost credits; earlier passes ruled a full eval out for credit reasons and this pass has no instruction to spend. **Plan: do not run them; run the deterministic parts (unit tests, the pipeline in `LLM_MODE=fake`) and record the real-model runs as deferred** (new items in `tasks/deferred.md`: smoke for `nexus.system` v3, review + first measured run of `tools.routing`). Expected movement when they are run: `chat.*` and `ops.*` unchanged within noise (the eval graph has no tools); `tools.*` measured for the first time.
- New dataset records: `tools.routing` 30 (AI-drafted, developer review pending).
- Tracing: tool calls already run inside the run's trace. The search query is user-approved text; it is masked by the existing `mask` on trace inputs. Web results are not logged. The Tavily key is a `SecretStr`, never logged (redaction already knows the `tvly-` shape).

## Uncertainties

- Several `interrupt()` calls in one node (two `web_search` calls in one message): LangGraph matches resume values by order; verify with a test before relying on it.
- Replaying a stored AI message whose tool call was not edited is fine; an edited query is NOT written into the AI message (it would rewrite history the reasoning item belongs to): the ToolMessage tells the model the user changed the query.
- Expired approval: stays in `pending_interrupt` (the card shows "Expired") until a new message abandons it; resume returns 409 `NO_PENDING_INTERRUPT`.
- Tavily response shape and `Authorization: Bearer` support come from Tavily's public docs, not from a live call (no live call is made in this pass: it spends credits and sends a query to a third party).
- Playwright needs the Compose stack (`make up`) with `LLM_MODE=fake` and `TAVILY_API_KEY` empty (the fake searcher is used in fake mode).

## What Will Be Skipped

- TASK-024 (tool cards in history, `ToolCard.tsx`): its own task. The approval card is the only new UI here; existing tool events keep their current handling.
- Real-model eval runs, review and baselines of `tools.routing` (credit, developer review); recorded as deferred items.
- 100 web searches per user per day (threat model matrix): needs a counter with a day window and a UI message; every search is already approved one by one, capped at 6 per turn and under the 30/min send limit. Deferred item.
- `research_plan` and `forget_memory` interrupts (v1.1, M7): the node and payload are written so they can be added, but only `web_search` exists.
- A live Tavily call.

## Risks

- A parked run touches the run lifecycle (`_settle`, `_finish`, usage, lease). Blast radius: every chat run. Mitigation: the existing 1011 tests must stay green; new behaviour only when the graph reports an interrupt.
- Changing `nexus.system` moves all chat behaviour: gates were not re-measured (see above); the risk is stated in the report, not hidden.
- Dangling tool calls in a thread make the next model call fail with a 400. The repair step addresses it and is tested (abandon, crash, cancel).
- The Tavily key must not leak into logs or errors: tool failures never include exception text.
- The Playwright run needs the whole stack; if it cannot be started here, say so rather than claim e2e passed.

## Ready to implement?

Yes: all sections filled. (The developer review step in `tasks/AGENTS.md` cannot happen in this session; the user asked for the tasks to be implemented and the plan follows the implementation plan's own description of both tasks.)
