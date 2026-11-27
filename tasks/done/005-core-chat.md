# Task: 005-core-chat

**Status:** done (pass 1: TASK-009, TASK-010; pass 2: TASK-012, TASK-011; pass 3: TASK-014, TASK-013)
**Created:** 2026-11-21
**ADR refs:** ADR-0002 (graph, checkpointer), ADR-0005 (model factory), ADR-0006 (SSE, run lease, drain), ADR-0007 (job queue), ADR-0008 (repo-canonical prompts), ADR-0004 (tracing, redaction), ADR-0009 (coverage gates)

---

## Goal

Build the AI foundation for chat: a single model factory with a scripted fake model, prompt loader, tracing/redaction/pricing helpers (TASK-009), the tool-less LangGraph chat graph with summarisation and a cache-friendly prompt layout (TASK-010), the Postgres job queue, worker and periodic tasks (TASK-012), and the streaming chat endpoints with the run lease (TASK-011), input moderation, the per-user rate limit and the daily budgets with `/me/usage` (TASK-014), and the chat UI with its hand-written stream client (TASK-013).

## Background

Implementation plan v1.3 (`docs/02_outputs/05_implementation_plan.md`), milestone M2, roadmap group `005-core-chat`. Pass 1 covered **TASK-009** and **TASK-010**; pass 2 covers **TASK-012** (done first, because TASK-011's run end enqueues its title job) and **TASK-011**. Pass 3 covers **TASK-014** (first, because the UI renders its refusals and shows `/me/usage`) and **TASK-013**. M1 (`004-skeleton`) still has TASK-003/033/034 open; they are not touched here.

## Acceptance Criteria

Copied from the plan.

**TASK-009 Model factory, fake model, prompt loader, tracing**
- [x] Unit tests with the fake model; the factory never sends `temperature` with an effort other than `none`
- [x] `mask` unit tests cover emails, phone numbers, card/IBAN-like numbers, JWTs and API keys
- [x] Verify whether multi-turn tool calls with `store=false` need encrypted reasoning items; record the answer in `memory/ai.md` *(answer: yes. Verified from `langchain-openai` source and OpenAI docs/forum; NOT by a live API call, `OPENAI_API_KEY` is empty locally. The first real tool loop in M3/M4 confirms it.)*

**TASK-010 Chat graph v1**
- [x] Graph unit tests with the fake model cover a multi-turn thread and summarisation triggering
- [x] A test asserts the message prefix is byte-identical across two consecutive turns (cache-friendliness)

**TASK-012 Job queue, worker & periodic tasks**
- [x] Two workers never process the same job (concurrency test), including a job that runs longer than the stale window *(scaled time: 0.1 s heartbeat, 0.5 s window, 1.5 s job, with a negative control that shows the job DOES run twice without the heartbeat; a real 5-minute job is not run in a unit test)*
- [x] A title appears after the first exchange *(HTTP send queues the job, a real worker with the real registry and the fake model sets the title; also checked on the Compose stack)*
- [x] A lease older than 60 s is reaped into a `failed` message with `finish_reason=server_restart` *(boundary tested at 59/61 s; the process-kill test below uses a 1 s window)*

**TASK-011 SSE streaming endpoint & run lease**
- [x] Event order matches the spec (the contract test parses the live stream and validates every frame against `StreamEvent` in the spec)
- [x] Cancel persists the partial answer as `cancelled` *(service and live-socket tests; the thread also receives it)*
- [x] Client disconnect does not kill the run *(live socket: drop the connection after 3 tokens, the full answer still lands)*
- [x] Killing the API mid-run leaves the conversation usable after the reaper runs *(real `SIGKILL` of a uvicorn subprocess, then the reaper, then a fresh API instance answers in the same conversation)*

**TASK-014 Moderation, rate limits, budgets**
- [x] Exceeding either budget returns 429 `BUDGET_EXCEEDED` with `Retry-After` and `details.scope` *(user and global; HTTP tests, a contract test validating the body against the spec's `Error` schema, and service tests with a real Postgres)*
- [x] Moderation fails open when the API is down, logged and tagged *(stub-client tests for timeout, 5xx, connection error, unreadable response, no key; the `moderation:skipped` trace tag is asserted on the run's trace; fake-mode markers drive the HTTP path)*
- [x] Per-user message rate limit *(30/min, atomic, parallel-burst test; regenerate counts)*
- [x] `/me/usage` and the sidebar usage meter; spend alert at 80 % of the global cap *(a WARNING log line once per day; not routed to a channel, see Decisions)*

**TASK-013 Chat UI**
- [x] `stream.ts` unit tests run against recorded stream fixtures (chunk boundaries inside frames, pings, errors) *(`src/api/stream.test.ts` + `fixtures/send-stream.txt`; the fixture is re-chunked at every size from 1 to 40 characters and at byte level, including a multi-byte character split across chunks)*
- [x] Playwright `chat.spec.ts` (fake LLM): send, stream, stop, regenerate *(plus moderation refusal, code/HTML rendering under the real CSP, and the usage meter; 6 tests)*

## Dependencies

- **Tasks:** TASK-004, TASK-005 (done, in `004-skeleton`)
- **Memory files:** `memory/ai.md`, `memory/project.md`
- **Docs:** `08_ai_system_design.md` §2–§4, §10; `09_eval_plan.md` (no suite yet); `10_llm_threat_model.md` (no new tools)

## AI Impact

- **Prompts touched:** new `nexus.system` v1, `nexus.context` v1, `nexus.summarize` v1 (`nexus.title` arrives with TASK-012)
- **Models / params changed:** factory defines every role (chat, summarize, title, memory_extract, judge, router, long_form) from env; no real model is called in this pass
- **Graph / tools / retrieval changes:** `load_context → (summarize?) → agent → END`, no tools yet
- **Eval datasets affected / added:** none — the eval harness is TASK-015, so `make eval` cannot run yet (`evals/eval-baseline.json` `metrics` is empty, nothing to regress)
- **Threat model entries:** none (no new tools). Untrusted-data policy for `<summary>` / `<user_memory>` lives in `nexus.system`
- **Expected cost / latency delta:** none until TASK-011 wires it to a request

## Files Expected to Change

See `005-core-chat.scratch.md`.

## Progress log

- 2026-11-21 **TASK-009 done.** `app/llm/`: `factory.py` (7 roles, Responses API, `store=False`, `include=["reasoning.encrypted_content"]` when effort ≠ `none`, temperature only at `none`, fallback after tool binding, router without retries/fallback, ruff `TID251` ban on constructing provider clients elsewhere), `fake.py` (+ JSON fixtures), `prompts.py` (+ `prompt_sync.py`, `scripts/sync_prompts.py`), `tracing.py` (Langfuse v5; masked; per-node spans verified with an in-memory exporter), `redaction.py`, `pricing.py` (Decimal), `tokens.py`. Prompts `nexus.system|context|summarize` v1. Config gained the `LLM_*` settings and `OPENAI_API_KEY`.
- 2026-11-21 **TASK-010 done.** `app/graph/`: `load_context → (summarize?) → agent → END`, injectable models, cache-friendly request layout, summarisation at 24k keeping ≤ 12k aligned on a human message. Integration test against the Postgres checkpointer with `LANGGRAPH_STRICT_MSGPACK=true`, including an AI message with an encrypted reasoning block round-tripping intact.
- Verification: `ruff`, `ruff format`, `mypy --strict` clean; 422 backend tests pass (unit + integration against the Compose Postgres on 5433). New packages: `app/llm` 95.2%, `app/graph` 91.7% (statements + branches), recorded as gates. Backend image rebuilt and checked offline as the non-root user (prompts load, tiktoken encoding baked in, fake graph answers).
- Mutation checks (change the code, the test must fail): temperature rule (found the first tests did NOT cover it: no real role pairs a temperature with an effort; added direct tests, now 4 failures), prompt-sync version-bump guard, summariser `nostream` tag, prefix stability (two mutations).
- **Found and fixed while testing:** the first `temperature` tests passed against a mutated factory (gap above); a test helper matched `<context>` inside the system prompt text.
- **Not done / honest limits:** `make eval` cannot run (eval harness is TASK-015; `evals/eval-baseline.json` has no gates), so no AI-behaviour regression gate exists yet. No real model call and no live Langfuse trace were made. Frontend untouched, so `make verify` was not run. For several small modules (pricing, redaction, prompts, tokens, prompt_sync) tests and code were written in the same step rather than with an observed red run; their rules were then protected with mutation checks where they matter.

- 2026-11-21 **TASK-012 done.** `app/jobs/{queue,handlers,periodic}.py`, `app/worker.py`, prompt `nexus.title` v1, `llm/usage.py` (per-run usage collector), `db/repositories/{usage,messages}.py`. See `memory/ai.md` (Background Jobs).
- 2026-11-21 **TASK-011 done.** `services/runs.py` (`RunService`), `api/{chat,sse}.py`, `core/shutdown.py`, lifespan wiring in `main.py` (LangGraph connection pool, tracer, SIGTERM hook), Dockerfile `--timeout-graceful-shutdown 105`, `LLM_FAKE_TOKEN_DELAY_S`. Added beyond the task text: `GET /conversations/{id}/messages` (spec'd, owned by no task, needed to observe the read model and by TASK-013), conversation delete now cancels the active run (removes the TODO), `periodic` extras (`requeue_stale_jobs`, `cleanup_rate_limits`).
- Verification: `ruff`, `ruff format`, `mypy --strict` clean; **563 backend tests pass** (was 422; unit + integration against the Compose Postgres on 5433; contract suite also green with `CONTRACT_FUZZ=random`); frontend lint/typecheck/77 unit tests and `make verify` (11 Playwright tests, 6 DOM checks) green against the rebuilt API. New coverage gates: `app/jobs` 96, `app/services` 90 (existing gates held: `app/llm` 95.9, `app/graph` 91.7). **Smoke on the real Compose stack (fake LLM via an override file):** register, chat through Caddy, worker set the title; SIGTERM sent to the API mid-answer: the stream completed (121 tokens, `usage`, `done`), the message was saved `complete`, container exit code 0 after ~8 s. The stack was then restored to its normal config and the smoke rows removed.
- Mutation checks (change the code, a test must fail): `SKIP LOCKED` (the first concurrency test did NOT catch it; added a deterministic held-row-lock test, now caught), `locked_by` guards, per-job heartbeat, default-title guard, owner filters (title job, message history), lease-lost check, cancel not appending to the thread, regenerate not replacing the row, drain not refusing, run heartbeat, title for failed turns, cancel ignored, SSE `id:` line, cost serialisation, ping frame, disconnect cancelling the run, SIGTERM chain (both links).
- **Found and fixed while testing:** (1) the first `SKIP LOCKED` test was vacuous (above); (2) NUL in `display_name` or an email returned 500 on PATCH /me, register and login (pre-existing; surfaced when the contract scope grew), now 422 with tests; (3) the spec's `listMessages` lacked the `422` the other list endpoints got in 1.3.1 (spec 1.3.2, client regenerated); (4) a dev-stack mishap of mine: `docker compose up` without the shell's `POSTGRES_PORT=5433` recreated the Postgres container on the default port and it failed to start; restored with the right port, data intact in the volume, nothing lost.
- **Decisions for the developer (defaults taken, see scratch file U1…U10):** (a) **`Idempotency-Key` on send is accepted but NOT honoured** (the spec and `memory/api.md` promise 24 h replay; there is no storage and replaying a stream is undefined); (b) the reaper does not append to the thread (empty text), deviating from doc 08 §2.3, which I corrected; (c) regenerate replaces the old row whatever the outcome; (d) tighten the spec for NUL (see 004-skeleton note) instead of the contract exclusions; (e) `READ_ONLY_MODE` is not enforced by the new endpoints (not in either task's text).
- **Not done / honest limits:** `make eval` still cannot run (harness is TASK-015), so no AI-behaviour regression gate exists; no real model call and no live Langfuse trace (no API key; every test and the smoke used the fake model); the 503 `SERVICE_RESTARTING` path is tested in-process but not observed live (I expect uvicorn to close its listening socket at SIGTERM, so a new request would meet a refused connection or a Caddy 502, and the 503 would only reach clients on already-open keep-alive connections; that expectation is unverified); `runs/resume`, `route_selected` and tool events are not built (no tools or router yet); moderation, rate limits and budgets are TASK-014; the `jobs` table is never pruned; `messages.trace_id` is not filled yet. For a few small pieces (`sse.py`, `shutdown.py`, `usage.py`) tests and code were written in the same step rather than with an observed red run; they were then mutation-checked.

- 2026-11-21 **TASK-014 done.** `core/budgets.py` (policy), `services/usage.py` (spend reads, `/me/usage`, 80 % alert), `core/ratelimit.py::hit` (atomic), `llm/moderation.py` (`Moderator`, fail-open) + `factory.build_moderation_client` + `fake.FakeModerationClient`, `RunService._admit`, `GET /me/usage`, `RunTrace.tags`, settings `USER_DAILY_BUDGET_USD`, `GLOBAL_DAILY_BUDGET_USD`, `CHAT_RATE_LIMIT_PER_MIN`. Spec 1.3.3 (`sendMessage` 400 may be `MODERATION_FLAGGED`), client regenerated.
- 2026-11-21 **TASK-013 done.** `src/api/stream.ts`, `features/chat/*` (ChatView, useChatRun, MessageList, Composer, Markdown, CodeBlock, highlight, notices, RunNotice, UsageMeter), new `HomePage` and `ConversationPage`, usage meter in `AppShell`; dependencies `react-markdown`, `remark-gfm`, `shiki` (lockfiles regenerated with `make npm-lock`).
- Verification: `ruff`, `ruff format`, `mypy --strict` clean; **626 backend tests pass** (was 563; unit + integration against the Compose Postgres on 5433, contract suite included and green); frontend lint, typecheck and **166 unit tests** pass; `make verify`: **17 Playwright tests** (11 existing + 6 new chat tests) and 6 DOM checks green against the rebuilt stack running the fake LLM (throw-away Compose override, removed afterwards); the chat spec was run 3 more times in a row without a flake. Coverage gates (all held): `app/llm` 96.3, `app/graph` 91.7, `app/jobs` 96.1, `app/services` 91.3, `app/core` 96.2; frontend `src/api` 97.2, `src/features/chat` 87.8 (statements). New/raised gates in `tests/coverage-baseline.json`.
- Mutation checks (change the code, a test must fail): rate-limit off-by-one and a non-atomic check-then-record (the parallel-burst test caught it), budget `>` vs `>=`, user budget ignored, moderation skipped, moderation fail-closed, regenerate skipping the limits, regenerate counted as a message, global cost ignoring the day, alert every time, moderation trace tag dropped; SSE parser CR hold, decoder `stream` flag, stopping at a final event, unknown-type skip, close-without-final; markdown `urlTransform`, image rendering, highlighting while streaming, `rel`; composer draft restore, Shift+Enter, Stop not cancelling, old answer shown while regenerating, usage not refreshed, Retry on every answer, hand-off guard, StrictMode-safe abort, title refresh, final token flush, draft restore after a refused hand-off.
- **Found and fixed while testing:** (1) the real browser run showed the stored reply and the live bubble on screen together for a moment, and the live one had missed its final batch of tokens: the live view now yields to a stored message by id in the same render and flushes the last tokens before waiting for the history read (unit tests for both); (2) a first message refused after the hand-off from the home page lost the user's text (the home composer had cleared and the chat page's composer is a new instance): the draft now lives in the page; (3) a first draft of my own e2e probe for the fake LLM compared the joined stream text incorrectly (tokens arrive as separate frames); (4) one StrictMode-safety test and two assertions on elements that the swap replaces were too weak/flaky at first (a mutation survived, and 3 of 6 runs failed) and were rewritten (`seen()`, a stream held open across a macrotask); (5) the composer showed a double focus ring and the dark-mode code block had no boundary (visual check with screenshots in both themes).
- **Decisions for the developer (defaults taken, see scratch file pass 3, U1…U15):** (a) the **global cap is reported before the user budget** when both are exceeded; (b) **regenerate counts toward the 30/min limit and the budgets** (it costs a model call), but is not counted as a "message sent" in `/me/usage`; (c) the **80 % spend alert is only a log line** (`global_spend_alert`, once per UTC day): wiring it to a channel is an ops step (TASK-034); (d) **Shiki highlighting uses 4 colours** (keyword, string/constant, comment, plain) taken from the design tokens, because stock Shiki themes need inline styles and the CSP forbids them; (e) Stop calls the cancel endpoint, not `abort()`; "Connection lost" offers Reload, never a resend; (f) the Playwright chat spec **fails** (does not skip) when the stack is not in `LLM_MODE=fake`.
- **Not done / honest limits:** `make eval` still cannot run (harness is TASK-015): no AI-behaviour regression gate exists, and moderation has no red-team record yet (rt-17); **no real OpenAI moderation call and no real model call were made** (no API key), so the real moderation response is only covered by a stub of its shape; `/settings/usage` and the settings page are not built (the meter is in the sidebar as the task says); feedback, files, tool cards, approvals and citations have no UI because the events do not exist yet (the stream client ignores them); the layout is not responsive below the sidebar width (pre-existing: the app shell has a fixed 17 rem sidebar); no index on `usage_daily(day)` for the global sum (rows = users × days, a migration for no gain at this size). For the Markdown, highlight, composer and notice modules I wrote implementation and tests close together rather than watching every test fail first; the mutation checks above were then used to prove the tests catch the behaviour (one survivor, "second send allowed" in the hook, is unreachable from the UI because the composer already blocks it; it is a defence against double clicks and stays).

## Notes

- The group is finished (TASK-009 … TASK-014): task and scratch files moved to `tasks/done/`.
