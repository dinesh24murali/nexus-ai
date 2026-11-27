# Task: 008-tools-hitl

**Status:** done (TASK-022 and TASK-023; the real-model eval criteria are **deferred** to `tasks/deferred.md` (D10-D14) and block nothing; TASK-024 "Tool-call UI" is NOT part of this pass)
**Created:** 2026-11-21
**ADR refs:** ADR-0005 (models only through the factory), ADR-0006 (run lease, SSE; a paused run holds no lease), ADR-0008 (prompts live in the repo), ADR-0009 (two-tier gates)

---

## Goal

Give the chat model two more tools, a safe `calculator` and an approved `web_search` (Tavily), and make every web search wait for the user's approve / edit / reject decision, with the pending approval surviving reloads and expiring after 24 h.

## Background

Implementation plan v1.3, milestone M4, roadmap group `008-tools-hitl`. This pass covers **TASK-022** (calculator and web search) and **TASK-023** (interrupt / resume flow). Functional spec UF-005 / FS-005, AI design doc 08 sections 2.2, 2.3, 2.4, 4 and 5, threats LLM-T02 (injection through web results), LLM-T07 (exfiltration through tool arguments), LLM-T08 (tool loops), LLM-T15 (code execution through the calculator). Builds on TASK-019 (`tools` node, citations, tool cap) and TASK-011 (runs, lease, SSE). The OpenAPI spec already has `resumeRun`, `PendingInterrupt`, `InterruptEvent` and `Conversation.pending_interrupt`.

## Acceptance Criteria

**TASK-022 Calculator & web search tools** (scratch: `008-tools-hitl.scratch.md`)
- [x] Calculator fuzz tests show no code execution (`tests/graph/test_calculator.py`: 85 attack strings, two seeded fuzzers, `eval`/`exec` patched out; mutation-checked: a version that lets `Compare`/`Attribute`/`Subscript` through fails 14 tests, one that falls back to `eval` hangs the suite on `9**9**9`)
- [x] `web_search`: Tavily client with a 10 s timeout, at most 5 results, domain metadata, results wrapped as untrusted `<web_result>` data, web citations (**request shape from Tavily's public docs; no live call was made**, D12)
- [x] `nexus.system` v3 has the tool-routing section (doc 08 section 4)
- [-] Tool-routing suite seeded and reviewed; first measured baselines recorded: **seeded (30 AI-drafted records, `evals/datasets/tools.routing.jsonl`) and wired (`evals/tools.py`, `make eval` runs it), NOT reviewed, NOT measured on a real model; deferred (D11)**. `tools.approval_enforced` needs no real model and is gated
- [-] `make eval` / `make eval-smoke` after the prompt change (AGENTS.md 3.8): **not run, deferred (D10)**; only the pipeline in `LLM_MODE=fake` was run

**TASK-023 Interrupt / resume flow**
- [x] Unit test (fake LLM): one message with `[search_my_files, web_search]` -> no call runs before the decision; on reject every `tool_call_id` still gets a ToolMessage (`tests/graph/test_approval.py`; mutation-checked: skipping the approval node fails 25 tests, a `tools` node that ignores the policy fails 19)
- [x] `tools.approval_enforced = 1` (absolute gate recorded in `evals/eval-baseline.json`; 3 scripted probes + every `web_search` call of `tools.routing`; a mutation test shows it can fail)
- [x] `pending_interrupt` persisted on the conversation, no run lease while waiting; resume with approve / edit / reject; 24 h expiry; the card is restored on reload (`tests/services/test_runs_approval.py`, `tests/api/test_chat_tools.py`, contract tests, `ApprovalCard` + chat component tests)
- [x] Playwright `tools.spec.ts` (fake LLM): approve, edit and reject paths, plus reload mid-approval (7 tests, real browser against the Compose stack in `LLM_MODE=fake`; the whole e2e suite, 28 tests, passes)

## Results

- Backend: see the CHANGELOG entry for the numbers. Frontend: 233 unit tests (208 before). Contract tests include `runs/resume`
- Done beyond the criteria: kill switch `TOOLS_DISABLED` enforced per run (also for an approval already waiting), `_repair_thread` (a thread left with an unanswered tool call is repaired before the next model call: abandoned approval, crash, stop between model and tools), spec 1.3.5 (`resumeRun` description and 429), `tool_log` in graph state, threat-model implementation status table, `AGENTS.md` conventions

## Dependencies

- **Tasks:** TASK-019 (tools node), TASK-011 (runs, SSE), TASK-013 (chat UI)
- **Memory files:** `memory/ai.md`, `memory/api.md`
- **Docs:** `docs/02_outputs/08_ai_system_design.md` sections 2.2-2.4, 4, 5; `docs/02_outputs/10_llm_threat_model.md`; `docs/02_outputs/09_eval_plan.md` section 9

## AI Impact

- **Prompts touched:** `nexus.system` 2 -> 3 (tool routing, web results are data, `[n]` for web citations, declined search = answer without it)
- **Models / params changed:** none
- **Graph / tools / retrieval changes:** new `approval` node and `TOOL_POLICY`; `tools` node runs three tools; `calculator`, `web_search`; interrupts; resume
- **Eval datasets affected / added:** `evals/datasets/tools.routing.jsonl` (new); `chat.golden`, `perf.latency`, `safety.redteam` run against the new system prompt
- **Threat model entries:** LLM-T02, T07, T08, T15 (matrix rows exist; red-team records are listed in Notes)
- **Expected cost / latency delta:** a longer system prompt (about +250 tokens); calculator and file search are free of USD; Tavily cost is per credit, not tracked in `usage_daily`

## Files Expected to Change

See `008-tools-hitl.scratch.md`.

## Notes

- **Not done / deferred:** `tasks/deferred.md` D10 (smoke run for `nexus.system` v3), D11 (review + first real run of `tools.routing`), D12 (live Tavily and real-model tool-loop checks), D13 (100 searches per user per day, red-team records for the new tools), D14 (Tavily spend, paused-run tokens on the message)
- **Behaviour to know:** an approval is consumed when the resume is accepted; if the resumed run then fails the user asks again. A resumed answer's `usage` covers the resumed run only. A paused run cannot be cancelled (there is no run): reject it
- **Decision (scratch uncertainty):** an edited query is NOT written into the model's own message; the ToolMessage tells the model the user changed it
