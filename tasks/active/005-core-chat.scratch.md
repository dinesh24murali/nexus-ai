# Scratch: 005-core-chat (TASK-009 + TASK-010)

> Written before any code, per AGENTS.md §3.1. The developer asked to implement "the next 2 tasks" and stop, so I proceed after writing this; open questions below are decided with a stated default and flagged in the final report.

---

## Plan

TDD, one slice per step; each ends green on `make lint` + `make test`.

**TASK-009**
1. **Deps + config.** `uv add langchain-openai langchain-core langfuse tiktoken pyyaml` (+ dev `types-pyyaml`). Add the `LLM_*` settings from `.env.example` to `core/config.py` (models, efforts, timeout, retries, router/long_form, `openai_api_key`). Ruff `TID251` banned-api rule: provider clients (`openai`, `langchain_openai.ChatOpenAI`/`OpenAIEmbeddings`) only importable in `app/llm/factory.py`.
2. **Pricing** (`llm/pricing.py`): per-1M table (input / cached / output; output includes reasoning), `cost_usd(model, usage)`. Unknown model → most expensive known rate + warning (budgets must never under-count).
3. **Redaction** (`llm/redaction.py`): `mask()` for emails, phone numbers, card/IBAN-like numbers, JWTs, API keys; recursive over dict/list/str; never raises. `hash_user_id()` = HMAC-SHA256(`APP_SECRET`, user_id).
4. **Prompt loader** (`llm/prompts.py`): `backend/prompts/<name>.md`, front-matter `name` + integer `version` (+ extra keys such as `min_confidence`), strict `{{var}}` rendering (missing or unused variable raises), cached. Prompts: `nexus.system`, `nexus.context`, `nexus.summarize`.
5. **Fake model** (`llm/fake.py`): `FakeChatModel(BaseChatModel)`; stateless script selection (turn index = AI messages since the last human message, so agent→tool→agent loops and resumes work), fixture chosen by a `[[fixture:<id>]]` marker in the newest human message (default `default` = echo), JSON fixtures in `app/llm/fixtures/` (text, tool calls, usage), per-token delay, streaming + `bind_tools` + `with_structured_output`-compatible, deterministic usage metadata.
6. **Factory** (`llm/factory.py`): `get_model(role, *, tools=None, settings=None)`; Responses API, `store=False`, `include=["reasoning.encrypted_content"]` (if verified), per-role effort / max output / timeout / retries; `temperature` only when effort `none`; fallback via `.with_fallbacks()` (applied after tool binding); router has no retries/fallback; `LLM_MODE=fake` returns the fake model. `get_embeddings()` / moderation client are **not** built here (M3 / TASK-014 are their first users).
7. **Tracing** (`llm/tracing.py`): Langfuse v5 client with `mask`, callback handler factory, per-node spans enabled explicitly, run metadata (`name`, `session_id`, hashed `user_id`, tags, prompt name + version), eval mode = no per-node callbacks. No keys → no-op; export errors never reach a run.
8. **`scripts/sync_prompts.py`**: pushes prompts whose version changed to Langfuse (label `production`); dry-run mode; unit-tested against a stub client.
9. **Verify `store=false` question** from OpenAI docs + `langchain-openai` source (no API key available for a live probe); answer recorded in `memory/ai.md`.

**TASK-010**
10. **Token counting** (`llm/tokens.py`): `o200k_base` via tiktoken; if the encoding cannot be loaded (offline) fall back to chars/4 with one warning.
11. **State + nodes** (`graph/state.py`, `graph/nodes/{load_context,summarize,agent}.py`, `graph/builder.py`): `ChatState` (messages, summary, memories, tool_calls_this_turn); `load_context` resets per-run fields (memories stay empty until M5); `summarize` runs when history > 24k tokens, keeps the most recent ≤ 12k tokens aligned on a human message, never splits tool-call/ToolMessage pairs, removes the rest with `RemoveMessage`; `agent` assembles the request in the cache-friendly order and calls the chat model; `build_graph(checkpointer, *, chat_model, summarize_model)` with injectable models.
12. **Tests:** multi-turn thread, summarisation triggering, prefix byte-identical across consecutive turns (also across a summary), tool-call pair safety, plus one `integration` test with `AsyncPostgresSaver` (strict msgpack on).
13. **Wrap-up:** `memory/ai.md` (prompt registry, factory/tracing facts, `store=false` answer, deviations), `CHANGELOG.md`, `tests/coverage-baseline.json` (`app/llm`, `app/graph` gates = measured), `backend/AGENTS.md` structure, Dockerfile (`COPY prompts`, tiktoken cache baked at build), `.env.example` if new vars.

## Files to Create or Modify

| File | Action |
|------|--------|
| `backend/pyproject.toml`, `backend/uv.lock` | modify (deps, ruff banned-api, per-file ignore) |
| `backend/app/core/config.py` | modify (LLM settings) |
| `backend/app/llm/{__init__,pricing,redaction,prompts,fake,factory,tracing,tokens}.py` | create |
| `backend/app/llm/fixtures/*.json` | create |
| `backend/prompts/{nexus.system,nexus.context,nexus.summarize}.md` | create |
| `backend/app/graph/{__init__,state,builder}.py`, `backend/app/graph/nodes/{__init__,load_context,summarize,agent}.py` | create |
| `scripts/sync_prompts.py` | create |
| `backend/tests/llm/*`, `backend/tests/graph/*`, `backend/tests/test_sync_prompts.py` | create |
| `backend/Dockerfile` | modify (copy prompts, bake tiktoken cache) |
| `memory/ai.md`, `memory/_index.md` (only if wording changes), `CHANGELOG.md`, `tests/coverage-baseline.json`, `backend/AGENTS.md`, task file | modify |

## Uncertainties and the defaults I'm taking

- **U1 — Summarise at 24k vs history ≤ 12k (doc 08 §2.2 vs §3).** Trimming the model's view with a sliding window at 12k would change the prefix every turn between 12k and 24k and defeat caching (G4). Default: the agent sends the state's history unmodified (bounded by the 24k trigger); `summarize` keeps the most recent ≤ 12k tokens. So "history ≤ 12k" holds right after summarisation, not at every turn. Recorded in `memory/ai.md`; the developer may prefer a lower trigger.
- **U2 — Late context / summary message roles.** Both are `HumanMessage`s wrapping data in tags, not system messages: memories and summaries derive from user-authored text and must not gain system authority (LLM-T-untrusted-data rule).
- **U3 — Cache prefix is one turn behind by design.** Because the late context message sits before the newest user message, turn N's cached prefix ends where context_N began; turn N+1 reuses everything before it. The test asserts exactly that property (prefix up to the late context message is a byte-prefix of the next turn's).
- **U4 — `nexus.system` content.** Doc 08 lists tool-routing, PDF/research, `/memory` capabilities that don't exist yet. A prompt that advertises them would make the model claim missing capabilities, so v1 describes only what exists (chat; no tools; memory is saved automatically; scope rule; untrusted-data policy; no offers for other modes). Later tasks add sections with a version bump.
- **U5 — Fake-model fixture selection** via a marker in the user message (`[[fixture:id]]`). It works through the real HTTP path for Playwright without extra plumbing, and the fake mode is refused in production.
- **U6 — `store=false` + reasoning items.** Expected: required (reasoning items must travel with function calls, via `include=["reasoning.encrypted_content"]`, preserved in the AIMessage content, hence in the checkpoint). Will confirm from docs/source; cannot live-probe (empty `OPENAI_API_KEY` in `.env`).
- **U7 — Langfuse v5 API** is newer than my reference knowledge; I'll read the installed package rather than assume. If the per-node-span behaviour differs from the docs I'll record it.
- **U8 — `CHAT_MODEL_OVERRIDE` kill switch** is a runtime flag (async, DB-backed). The factory stays sync/pure: it accepts a `model_override` argument; the caller (graph/run service, TASK-011) passes the flag value. Not wired in this pass.

## What Will Be Skipped (and why)

- SSE endpoint, run lease, regenerate/cancel — TASK-011. Messages read model and per-message usage persistence — TASK-011 (pricing helper exists, nothing calls it yet).
- Tools, approval node, tool cap, citations state — M3/M4. The builder keeps a single seam (`agent → END`).
- `route` state field and intent router — M7.
- Embeddings and moderation clients — first users are TASK-025 / TASK-014.
- Eval harness and datasets — TASK-015; `make eval` is not runnable, noted honestly in the task file.
- Real model calls, live Langfuse traces — no API key; Langfuse keys exist locally but no trace is sent from tests.
- `nexus.title` prompt — TASK-012.

## Risks

- **langchain-openai API drift** (`use_responses_api`, `reasoning`, `max_tokens` → `max_output_tokens`, `include`): read the installed source and assert the exact request payload in tests, so wrong parameters fail locally rather than at the first real call.
- **tiktoken downloads its encoding on first use** → offline CI/containers. Mitigated by the fallback counter and baking the cache into the image.
- **Strict msgpack** may reject message types in the Postgres checkpointer; the integration test covers it.
- **Prefix stability** is easy to break silently (timestamps, dict ordering); the byte-identity test guards it.
- Blast radius: new modules only; existing API/auth code is untouched apart from `config.py`.

## Ready to implement?

Yes (per the developer's instruction); defaults above are flagged for review.

## Outcome (2026-11-21)

Implemented as planned. Resolutions: U1 kept as stated (agent sends unmodified state; documented in `memory/ai.md` and doc 08 §3). U3 confirmed by test. U6 answered yes (source + docs, no live probe). U7: Langfuse v5 exports node spans by default; made explicit and tested. U8 not wired (as planned). Dockerfile also copies `prompts/` and bakes the tiktoken cache. Embeddings, moderation, title prompt, tools, route state remain skipped as listed.

---

# Pass 2: TASK-012 (job queue, worker, periodic) then TASK-011 (SSE endpoint, run lease)

> Written before any code (AGENTS.md §3.1). The developer asked for "the next 2 tasks" and to stop afterwards. I do **TASK-012 first**: TASK-011's run end enqueues `generate_title` and both share the usage repository, and the queue is independent of the graph. TASK-013 (chat UI) and TASK-014 (moderation, rate limits, budgets) are out of scope.

## Plan (TDD: failing test, watch it fail, minimal code, green; one slice at a time)

**TASK-012: `app/jobs/`**
1. `jobs/queue.py`: `enqueue(session, kind, payload, idempotency_key, user_id=, max_attempts=, run_after=)` (INSERT … ON CONFLICT DO NOTHING, so enqueue joins the caller's transaction); `claim()` = `SELECT … status='queued' AND run_after<=now() ORDER BY run_after,id FOR UPDATE SKIP LOCKED LIMIT 1` then mark `running`, `attempts+1`, `locked_at/locked_by`; `heartbeat()`, `complete()`, `fail()` (retry with exponential backoff 10 s→2 min, or `failed` when attempts are spent), `requeue_stale(older_than)`. Every state change is conditional on `locked_by` so a worker whose job was requeued cannot complete someone else's run.
2. `jobs/handlers.py`: kind registry `{handler, timeout_s, max_attempts}`; `generate_title` (new prompt `nexus.title` v1, `title` role; only overwrites a title that is still `New chat`; never bumps `updated_at`; cost recorded against the user).
3. `jobs/periodic.py`: `PeriodicTask(name, interval_s, fn)` runner (an exception is logged, never kills the loop); tasks `reap_stale_runs` (30 s), `requeue_stale_jobs` (30 s, job heartbeat), `cleanup_invites` (daily), `cleanup_rate_limits` (daily; `rate_limits.py` says TASK-012 owns it).
4. `worker.py`: concurrency 4 claim loops, 1 s poll, per-job heartbeat task (`locked_at` every 30 s), hard per-kind timeout (`asyncio.timeout`), graceful shutdown (stop claiming; wait for in-flight jobs up to a grace; the rest go back to the queue). The existing heartbeat-file behaviour and test stay.
5. Reaper (`services/runs.py` or `jobs/periodic.py`, DB only): lease with `run_heartbeat_at` older than 60 s → `failed` assistant message (`finish_reason=server_restart`), lease cleared, in one transaction, `FOR UPDATE SKIP LOCKED` so two workers do not double-write.

**TASK-011**
6. `db/repositories/{messages,usage}.py` (owner-scoped; usage = `usage_daily` upsert in USD) and the lease operations (`acquire`, `heartbeat`, `release`) on the conversations repository.
7. `api/sse.py`: event models mirroring the spec's `*Event` components, frame encoder (`event:`, `id: <seq>`, `data:`), `: ping` every 15 s.
8. `services/runs.py` (`RunService`, on `app.state.runs`): acquire the lease atomically → insert the user message → run the graph as a **detached asyncio task** that pushes events into a queue the HTTP response drains. So a client disconnect never stops it. Heartbeat every 10 s. Finish = one transaction: release lease (if the lease is gone because the reaper took it, write nothing) + insert assistant message + `usage_daily` + `enqueue generate_title` on the first answer. Cancel (`POST /runs/cancel`): cancel the inner graph task, persist the partial text as `cancelled`, append it to the thread with `aupdate_state(as_node="agent")`, emit `done(finish_reason=cancelled)`. Regenerate: fork from the latest checkpoint whose `next == ("agent",)` and replace the last assistant row. Drain: `begin_drain()` → 503 `SERVICE_RESTARTING` + `Retry-After`; `drain(90 s)`; leftovers saved `failed`/`server_restart`; lifespan shutdown calls it; SIGTERM handler chained in front of uvicorn's so new runs are refused as soon as the signal arrives.
9. `api/chat.py`: `POST /conversations/{id}/messages`, `/regenerate`, `/runs/cancel`; `GET /conversations/{id}/messages` (spec'd, owned by no task, needed to observe the read model and by TASK-013). Delete-conversation now cancels an active run first (removes the TODO).
10. Contract tests: parse the live stream and validate every frame against `StreamEvent` in the spec; order `run_started → token* → usage → done`. Add the three operations to the schemathesis include list.
11. Wrap-up: `memory/api.md`, `memory/ai.md`, `memory/_index.md` (only if wording changes), `CHANGELOG.md`, `tests/coverage-baseline.json` (new packages `app/jobs`, `app/services` extended), `backend/AGENTS.md` structure, ADR-0006/0007 only if a decision changes (none expected), task file.

## Files (estimate)

| File | Action |
|------|--------|
| `backend/app/jobs/{__init__,queue,handlers,periodic}.py` | create |
| `backend/app/worker.py` | modify |
| `backend/app/services/runs.py`, `backend/app/api/{chat,sse}.py` | create |
| `backend/app/api/{schemas,deps}.py`, `backend/app/main.py`, `backend/app/api/conversations.py` | modify |
| `backend/app/db/repositories/{messages,usage}.py`, `…/conversations.py` | create / modify |
| `backend/app/llm/tracing.py` (job trace names), `backend/prompts/nexus.title.md` | modify / create |
| `backend/tests/jobs/*`, `backend/tests/services/*`, `backend/tests/api/test_chat.py`, `backend/tests/contract/*`, `backend/tests/conftest.py` (shared live-server fixture) | create / modify |
| `docs/02_outputs/04_api_spec.yaml` | only if a documented behaviour changes (see U3) |
| `memory/*`, `CHANGELOG.md`, `tests/coverage-baseline.json`, `backend/AGENTS.md`, task file | modify |

## Uncertainties and the defaults I'm taking

- **U1: reaper and the thread.** Doc 08 §2.3 says the reaper appends the failed message to the thread. A crashed run has no text to append (the partial text lived only in the dead process), and an empty AI message in the checkpoint is worse than none. Default: the reaper writes the `failed` read-model row (empty content) and does **not** touch the checkpoint; the next turn sees two user messages in a row, which is valid. Cancel and drain, where the API still has the text, do append it.
- **U2: assistant message id.** The id is promised in `run_started` and the reaper (a different process) must reuse it. Default: `assistant_message_id = uuid5(run_id, "assistant")`, so the reaper derives it from `active_run_id` with no new column.
- **U3: `Idempotency-Key` on send.** The spec and `memory/api.md` promise 24 h replay of the original result; there is no storage for it and replaying a *stream* is undefined. Default: header accepted, not honoured; a retry while the run is active gets 409 `RUN_IN_PROGRESS`. **Flagged for the developer**; honouring it needs a table or a spec change.
- **U4: `runs/resume`** (spec'd) is not built: there are no tools, so no interrupts exist until M4. Excluded from the contract test list with the reason.
- **U5: `route_selected` event** is not emitted: the router arrives in M7 and the spec says "new messages only" once it exists.
- **U6: regenerate outcome.** The old assistant row is replaced whatever the outcome (complete, cancelled, failed) because the forked thread no longer contains the old answer; keeping a stale row would desynchronise the read model from the checkpoint.
- **U7: stricter than the spec.** NUL in `content` and whitespace-only `content` return 422 (Postgres cannot store NUL; an empty prompt is a user error). Same pattern, same documented exclusion, as conversation titles. The spec's `minLength: 1` stays as is unless the developer wants it tightened.
- **U8: graph construction.** Lazy and cached per `CHAT_MODEL_OVERRIDE` value (the kill switch is read per run and passed as `model_override`). Building at startup would make the whole API fail to boot when `OPENAI_API_KEY` is empty in real mode.
- **U9: drain and uvicorn.** Uvicorn stops accepting, waits for open requests (streams hold until their run ends), then runs lifespan shutdown. The SIGTERM chain only sets the "refuse new runs" flag early; the real wait is uvicorn's graceful timeout (raised in the Dockerfile CMD, ≥ 100 s) plus `drain()` in lifespan shutdown for runs whose client already left.
- **U10: scaled-time tests.** "A job that runs longer than 5 min" cannot run for 5 min in a unit test. The property is "job duration » stale threshold while the heartbeat runs", so the test uses a 0.5 s threshold, 0.1 s heartbeat and a 1.5 s job. The production constants (30 s / 2 min) are asserted separately. Said plainly in the task file.

## What Will Be Skipped (and why)

- Moderation, per-user rate limit, USD budgets, `/me/usage`, usage meter: TASK-014 (the `usage_daily` upsert exists, nothing enforces it).
- Chat UI and `stream.ts`: TASK-013. The generated frontend client only changes if the spec changes (not planned).
- `runs/resume`, tools, approval, `route_selected`, `extract_memories`, `ingest_file`, `purge_user`, document jobs: later milestones.
- Real model calls and live Langfuse traces: no API key; every test uses `LLM_MODE=fake`.
- `make eval`: the harness is TASK-015, so there is still no AI-behaviour regression gate. Noted honestly in the task file.

## Risks

- **Concurrency bugs are invisible in single-threaded tests.** Queue claim, lease acquire and reaper get real two-session/two-worker tests against Postgres, plus mutation checks (drop `SKIP LOCKED`, drop the `locked_by` condition, drop the lease-lost check).
- **Cancelling an `astream` task** can leave the checkpoint mid-step; tested against the real checkpointer, including a following turn.
- **Event schema drift:** the contract test validates frames against the spec itself.
- **SIGTERM chaining** only works on the main thread; guarded, and covered by a unit test with a recorded previous handler.
- Blast radius: `main.py` lifespan (new services), `conversations.py` delete, `worker.py`. Existing auth/conversation tests must stay green.

## Ready to implement?

Yes (per the developer's instruction); defaults above are flagged for review in the final report (U3 especially).

## Outcome of pass 2 (2026-11-21)

Implemented as planned, TASK-012 first. Resolutions: U1 kept (and doc 08 §2.3 corrected); U2 kept; U3 kept and flagged (Idempotency-Key not honoured); U4/U5 kept; U6 kept; U7 kept (contract exclusions documented); U8 kept (graphs cached per override value); **U9 verified on the real stack** (a stream open at SIGTERM completes, container exits 0 in ~8 s); U10 kept (scaled-time tests with a negative control). Additions: `GET …/messages`, delete-cancels-run, `requeue_stale_jobs` and `cleanup_rate_limits`, `LLM_FAKE_TOKEN_DELAY_S`, spec 1.3.2, and a NUL-in-text fix found by the contract scope.
