# Task: 005-core-chat

**Status:** active (passes 1 and 2 done: TASK-009 … TASK-012; TASK-013 and TASK-014 remain)
**Created:** 2026-11-21
**ADR refs:** ADR-0002 (graph, checkpointer), ADR-0005 (model factory), ADR-0006 (SSE, run lease, drain), ADR-0007 (job queue), ADR-0008 (repo-canonical prompts), ADR-0004 (tracing, redaction), ADR-0009 (coverage gates)

---

## Goal

Build the AI foundation for chat: a single model factory with a scripted fake model, prompt loader, tracing/redaction/pricing helpers (TASK-009), the tool-less LangGraph chat graph with summarisation and a cache-friendly prompt layout (TASK-010), the Postgres job queue, worker and periodic tasks (TASK-012), and the streaming chat endpoints with the run lease (TASK-011).

## Background

Implementation plan v1.3 (`docs/02_outputs/05_implementation_plan.md`), milestone M2, roadmap group `005-core-chat`. Pass 1 covered **TASK-009** and **TASK-010**; pass 2 covers **TASK-012** (done first, because TASK-011's run end enqueues its title job) and **TASK-011**. The chat UI (TASK-013) and moderation/rate limits/budgets (TASK-014) are out of scope. M1 (`004-skeleton`) still has TASK-003/033/034 open; they are not touched here.

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

## Notes

- Not moved to `tasks/done/` after this pass: the group (TASK-013, TASK-014) is unfinished.
