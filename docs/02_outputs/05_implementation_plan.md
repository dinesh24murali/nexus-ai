# Implementation Plan — Nexus AI v1

**Version:** 1.3 · **Date:** 2026-11-21 (task 003b: M7 trimmed — schedules to backlog, slash commands, no `remember_fact`; doc 08 gap resolutions placed in tasks) · previous 1.2 2026-11-21 (M7 added), 1.1 2026-11-19 (task 003) · **Inputs:** [PRD](01_prd.md), [Architecture](03_tech_architecture.md), [API](04_api_spec.yaml), [AI design](08_ai_system_design.md), [Eval plan](09_eval_plan.md), [Threat model](10_llm_threat_model.md)

Work is organised by **milestone** (PRD §12). Milestones set the order, not dates; the builder is one developer working part-time with AI coding agents. **Every milestone ends deployed**: run the `deploy.yml` workflow and check `/readyz` and a chat smoke on the hosted instance.

Each repo task group in brackets becomes `tasks/active/<NNN>-<slug>.md` when started (task 003 was the plan revision, so groups start at 004). TASK ids are stable references; tasks added in revision 1.1 are numbered from 036, and M7 tasks (revision 1.2) from 038. TASK-044 and TASK-045 (schedules) were removed in revision 1.3; their ids are not reused.

---

## M1 — Skeleton, live on the VM  [004-skeleton]

#### TASK-001: Monorepo tooling
- **Depends on:** none
- **Files:** `backend/pyproject.toml`, `backend/uv.lock`, `frontend/package.json`, `frontend/package-lock.json`, `.pre-commit-config.yaml`, `Makefile`
- **Description:** Backend with uv on Python 3.14 (ruff, mypy --strict, pytest, pytest-asyncio, coverage); frontend with Vite 8 + React 19 + TypeScript 5.9 (eslint, prettier, vitest). Pin exact versions in the lockfiles; docs name majors only. Add `make lint`, `make test`, `make fmt`.
- **Acceptance criteria:**
  - [ ] `make lint` and `make test` pass on an empty project
  - [ ] pre-commit runs ruff, prettier, and a secrets scanner (gitleaks)

#### TASK-002: Docker Compose stack
- **Depends on:** TASK-001
- **Files:** `compose.yml`, `compose.prod.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `deploy/Caddyfile`, `.env.example`
- **Description:** Services: caddy (2.11), api (`stop_grace_period: 120s`), worker, migrate, postgres (`pgvector/pgvector:pg18`, volume at `/var/lib/postgresql`), qdrant (1.x), with healthchecks and named volumes. Optional profile `langfuse` with self-hosted Langfuse v4 (web, worker, ClickHouse, Redis, MinIO, its own Postgres). Multi-stage, non-root images.
- **Acceptance criteria:**
  - [ ] `make up` brings every default service to healthy in < 2 min on a clean machine
  - [ ] API at `http://localhost:8000/api/v1/healthz`; traces reach the Langfuse Cloud dev project

#### TASK-003: CI pipeline
- **Depends on:** TASK-001 · *parallel with TASK-002*
- **Files:** `.github/workflows/ci.yml`, `.github/dependabot.yml`
- **Description:** CI is the staging environment. Run lint, type-check, unit and integration tests (Postgres + Qdrant service containers), contract tests, a full-stack Playwright smoke with `LLM_MODE=fake`, eval-smoke (AI changes, from M2), and image builds. On merge to `main`: push images to GHCR and sync prompts to Langfuse (ADR-0008). Add pip-audit, npm audit and Trivy.
- **Acceptance criteria:**
  - [ ] Pipeline is green on the first PR; required checks enforced on `main`

#### TASK-004: Config, logging, errors
- **Depends on:** TASK-001
- **Files:** `backend/app/core/{config,logging,errors}.py`, `backend/app/main.py`
- **Description:** pydantic-settings config from env (refuses to start with `APP_ENV=production` + `LLM_MODE=fake`); structlog JSON logging with a request-id middleware; error envelope and exception handlers matching the `Error` schema.
- **Acceptance criteria:**
  - [ ] Unknown routes return the error envelope with `request_id`
  - [ ] Logs carry no request bodies

#### TASK-005: Database layer & migrations
- **Depends on:** TASK-004
- **Files:** `backend/app/db/{session,models,base}.py`, `backend/alembic/`, `backend/app/db/repositories/`
- **Description:** Async SQLAlchemy models (psycopg 3, `postgresql+psycopg://`) for all tables in architecture §3.2 including `invites` and the run-lease columns; the initial Alembic migration; LangGraph checkpointer/store setup in the migrate step (psycopg async pool, `LANGGRAPH_STRICT_MSGPACK=true`). Repository functions all take `user_id`.
- **Acceptance criteria:**
  - [ ] `alembic upgrade head` on an empty DB creates all tables and indexes
  - [ ] The migrate service is idempotent

#### TASK-006: Auth, invites & admin CLI
- **Depends on:** TASK-005
- **Files:** `backend/app/api/auth.py`, `backend/app/core/security.py`, `backend/app/api/me.py`, `backend/app/cli.py`, `backend/tests/api/test_auth.py`
- **Description:** Register (invite code required when `REGISTRATION_MODE=invite`, the default), login, refresh (rotation + family revocation), logout; argon2id hashing; JWT (HS256, 15 min); `/me` GET/PATCH, password change; auth and invite-attempt rate limits. Admin CLI: `invite create|list|revoke`, `reset-password`, `disable-user`/`enable-user`, `flags set|list` for kill switches (`runtime_flags` table, read by the API and worker through a 60 s cache; env gives boot defaults; doc 10 §3).
- **Acceptance criteria:**
  - [ ] Refresh-token reuse revokes the whole family (tested)
  - [ ] Login does not reveal whether an email exists
  - [ ] Unknown, used, revoked and expired invite codes all return the same `INVITE_INVALID` (tested)
  - [ ] Contract tests for the auth endpoints pass
  - [ ] A flag set with the CLI takes effect in a running API within 60 s without a restart (tested)

#### TASK-007: Frontend shell
- **Depends on:** TASK-001 · *parallel with TASK-004–006*
- **Files:** `frontend/src/{main,App}.tsx`, `frontend/src/routes/`, `frontend/src/api/` (generated), `frontend/src/lib/auth.ts`, `frontend/src/styles/tokens.css`, `frontend/src/styles/theme.css` (Tailwind 4 `@theme`)
- **Description:** React Router 8 (SPA / declarative mode) with the screens from functional spec §3; `npm run gen:api` (openapi-typescript); auth context with in-memory access token and silent refresh; register page with the invite field prefilled from `?invite=`; design tokens from doc 07 (Tailwind 4 `@theme`); light/dark/system theme.
- **Acceptance criteria:**
  - [ ] Invite → register → login work end-to-end against the API
  - [ ] Playwright `auth.spec.ts` passes

#### TASK-008: Conversations CRUD
- **Depends on:** TASK-006
- **Files:** `backend/app/api/conversations.py`, `frontend/src/components/sidebar/`
- **Description:** List, create, get, rename and delete conversations with cursor pagination; sidebar with infinite scroll and rename/delete menu.
- **Acceptance criteria:**
  - [ ] Cross-user access returns 404 (isolation test)
  - [ ] Sidebar e2e test passes

#### TASK-033: Deploy workflow
- **Depends on:** TASK-002, TASK-003
- **Files:** `.github/workflows/deploy.yml`, `deploy/README.md`, `memory/infra.md`
- **Description:** Manually dispatched workflow: SSH to the prod VM, `docker compose pull && up -d` with GHCR images, then smoke `/readyz`. Caddy TLS; secrets in `.env` (0600) on the VM.
- **Acceptance criteria:**
  - [ ] The M1 skeleton is live on the VM; a deploy during an open browser session does not log the user out

#### TASK-034: Backups & uptime check
- **Depends on:** TASK-033
- **Files:** `deploy/backup.sh`, `deploy/uptime.md`
- **Description:** Nightly `pg_dump` and Qdrant snapshot to off-site storage, with one tested restore; one external uptime check on `/readyz`. No SLO and no alert rules beyond uptime and spend (TASK-014).
- **Acceptance criteria:**
  - [ ] A restore into a fresh local stack succeeds once and is documented

## M2 — Core chat + evals

### Core chat  [005-core-chat]

#### TASK-009: Model factory, fake model, prompt loader, tracing
- **Depends on:** TASK-004
- **Files:** `backend/app/llm/{factory,fake,prompts,tracing,pricing}.py`, `backend/prompts/*.md`, `scripts/sync_prompts.py`
- **Description:** `get_model(role)` per doc 08 §3: reasoning effort per role (temperature only at effort `none`), Responses API with `store=false`, timeouts, retries, fallback and the Langfuse callback (SDK v5; enable per-node spans). `LLM_MODE=fake` returns a scripted model (text, tool calls, interrupts; per-token delay) keyed by fixture id. Prompts load from `backend/prompts/` on disk (ADR-0008); `sync_prompts.py` pushes changed prompts to Langfuse in CI. HMAC user hashing; `mask` redaction; pricing table including cached and reasoning tokens. An eval tracing mode sends one trace per case with no per-node spans (doc 09 §6); the CI Langfuse project lives in its own organisation.
- **Acceptance criteria:**
  - [ ] Unit tests with the fake model; the factory never sends `temperature` with an effort other than `none`
  - [ ] `mask` unit tests cover emails, phone numbers, card/IBAN-like numbers, JWTs and API keys
  - [ ] Verify whether multi-turn tool calls with `store=false` need encrypted reasoning items; record the answer in `memory/ai.md`
- > **ADR:** ADR-0005, ADR-0008

#### TASK-010: Chat graph v1
- **Depends on:** TASK-005, TASK-009
- **Files:** `backend/app/graph/{state,builder}.py`, `backend/app/graph/nodes/{load_context,agent,summarize}.py`
- **Description:** The LangGraph graph from doc 08 without tools; `AsyncPostgresSaver`; trimming and summarisation (summary lives in graph state only); cache-friendly prompt layout (doc 08 §4: fixed system prompt → summary → history → late context message → newest user message). `nexus.system` includes the capability paragraph, the scope rule for questions about other users or the platform, and the "never claim to have saved or deleted a memory" rule (doc 08 §4).
- **Acceptance criteria:**
  - [ ] Graph unit tests with the fake model cover a multi-turn thread and summarisation triggering
  - [ ] A test asserts the message prefix is byte-identical across two consecutive turns (cache-friendliness)

#### TASK-011: SSE streaming endpoint & run lease
- **Depends on:** TASK-010, TASK-008
- **Files:** `backend/app/api/chat.py`, `backend/app/api/sse.py`, `backend/app/services/runs.py`
- **Description:** `POST /conversations/{id}/messages` streams typed events; one active run per conversation via the run lease (`active_run_id` + `run_heartbeat_at` every 10 s); cancel; regenerate; graceful drain on SIGTERM (503 `SERVICE_RESTARTING`, 90 s wait, leftovers saved as `failed`); messages read model persisted at run end; per-message usage.
- **Acceptance criteria:**
  - [ ] Event order matches the spec (contract test parses the stream)
  - [ ] Cancel persists the partial answer as `cancelled`
  - [ ] Client disconnect does not kill the run
  - [ ] Killing the API mid-run leaves the conversation usable after the reaper runs (integration test)
- > **ADR:** ADR-0006

#### TASK-012: Job queue, worker & periodic tasks
- **Depends on:** TASK-005 · *parallel with TASK-010*
- **Files:** `backend/app/jobs/{queue,handlers,periodic}.py`, `backend/app/worker.py`
- **Description:** Postgres SKIP LOCKED queue with retries, backoff, idempotency keys and graceful shutdown; job heartbeat (`locked_at` refreshed every 30 s, requeue after 2 min without one) and a hard timeout per job kind (doc 08 §9); `generate_title` handler; periodic `reap_stale_runs` (30 s) and `cleanup_invites` (daily).
- **Acceptance criteria:**
  - [ ] Two workers never process the same job (concurrency test), including a job that runs longer than 5 min
  - [ ] A title appears after the first exchange
  - [ ] A lease older than 60 s is reaped into a `failed` message with `finish_reason=server_restart`
- > **ADR:** ADR-0007

#### TASK-013: Chat UI
- **Depends on:** TASK-011, TASK-007
- **Files:** `frontend/src/api/stream.ts`, `frontend/src/features/chat/*`
- **Description:** Hand-written stream client (`fetch` + `ReadableStream` + line-based SSE parser + `AbortController`) typed from the generated `StreamEvent`; message list with sanitised markdown, Shiki code blocks and copy; composer with send/stop; regenerate; error states including "Interrupted" (`server_restart`) and `SERVICE_RESTARTING`.
- **Acceptance criteria:**
  - [ ] `stream.ts` unit tests run against recorded stream fixtures (chunk boundaries inside frames, pings, errors)
  - [ ] Playwright `chat.spec.ts` (fake LLM): send, stream, stop, regenerate

#### TASK-014: Moderation, rate limits, budgets
- **Depends on:** TASK-011
- **Files:** `backend/app/llm/moderation.py`, `backend/app/core/{ratelimit,budgets}.py`, `backend/app/services/usage.py`, `backend/app/api/me.py`
- **Description:** Input moderation before runs (fail-open when the API is down: logged + trace tag); per-user message rate limit; per-user daily spend budget (USD, default $0.50) and global daily cap (default $5) from the pricing table; background jobs count toward the user's spend; `/me/usage`; usage meter in the sidebar; spend alert at 80% of the global cap.
- **Acceptance criteria:**
  - [ ] Exceeding either budget returns 429 `BUDGET_EXCEEDED` with `Retry-After` and `details.scope`

### Eval harness  [006-eval-harness]

#### TASK-015: Eval runner & two-tier gate
- **Depends on:** TASK-010
- **Files:** `backend/evals/{run,judges,metrics,canaries}.py`, `evals/datasets/*.jsonl`, `evals/eval-baseline.json`
- **Description:** Run the graph against JSONL datasets (synced to Langfuse datasets), score with metrics and judges from doc 09, write `evals/results/latest.json`, and log runs to Langfuse. `--suite smoke|full`; a weekly scheduled workflow runs the full suite on `main` (also the drift alarm for alias-only models). AI agents draft dataset records; the developer reviews them. Record the first measured values as regression gates (ADR-0009); judged metrics stay advisory.
- **Acceptance criteria:**
  - [ ] `make eval-smoke` runs in < 2 min and gates on `eval-baseline.json`
  - [ ] Chat datasets are seeded and reviewed; first measured baselines recorded

#### TASK-036: Chat-model bake-off
- **Depends on:** TASK-015, TASK-014
- **Files:** `evals/results/bakeoff-<date>.md`, `memory/ai.md`, `.env.example`
- **Description:** Run the chat, tool-routing (as far as available), latency and cost suites on `gpt-6.1-terra` (effort `low`) and pinned `gpt-5.4-mini-2026-03-17`; re-check model names and prices first. Pick the model that meets G3 (TTFT), G4 (cost) and the quality targets; record the result. Re-run the tool-routing part in M4.
- **Acceptance criteria:**
  - [ ] Decision recorded in `memory/ai.md` with the measured TTFT p95, cost per turn and scores

#### TASK-016: Feedback
- **Depends on:** TASK-011
- **Files:** `backend/app/api/feedback.py`, `frontend/src/features/chat/Feedback.tsx`
- **Description:** `PUT /messages/{id}/feedback` stores the rating and sends a Langfuse score on the message's trace.
- **Acceptance criteria:**
  - [ ] The score is visible on the Langfuse trace

## M3 — RAG  [007-rag]

#### TASK-017: Upload API & storage
- **Depends on:** TASK-012
- **Files:** `backend/app/api/files.py`, `backend/app/services/files.py`
- **Description:** Multipart upload with size/type/magic-byte checks, SHA-256 dedupe, per-user limit; store on the uploads volume; enqueue `ingest_file`.
- **Acceptance criteria:**
  - [ ] All validation errors from functional spec FS-004 are covered by tests

#### TASK-018: Ingestion pipeline
- **Depends on:** TASK-017
- **Files:** `backend/app/rag/{parsers,chunker,embedder,index}.py`
- **Description:** Parse PDF (pypdf), DOCX (python-docx), TXT/MD; token-aware chunking (800/120); batched embeddings; idempotent Qdrant upsert with tenant payload; status updates.
- **Acceptance criteria:**
  - [ ] Re-running a job produces no duplicates
  - [ ] A failed parse sets status `failed` with a readable reason
- > **ADR:** ADR-0003

#### TASK-019: Retrieval tool & citations
- **Depends on:** TASK-018, TASK-010
- **Files:** `backend/app/rag/retriever.py`, `backend/app/graph/tools/search_my_files.py`, `backend/app/graph/nodes/tools.py`
- **Description:** `search_my_files` using Qdrant `query_points` with a mandatory `user_id` filter; returns numbered passages wrapped as untrusted data, each `<document>` tag carrying `file_id`; the late context message lists the user's 5 most recent ready files (id, filename, uploaded_at) so the model can pass `file_ids`; emits citation events. The tools node counts individual calls toward the cap of 6 and answers calls beyond it with "tool limit reached" (doc 08 §2.2).
- **Acceptance criteria:**
  - [x] Isolation test: user B never retrieves user A's chunks
  - [x] Tool-cap unit test (fake LLM): 4 + 3 parallel calls → 6 run, the 7th gets "tool limit reached", `finish_reason=tool_limit`
  - [-] Multi-step file-then-web cases (doc 09 §9) measured; decide whether a per-file synopsis or `get_file_overview` tool is needed (doc 08 §5) — **skipped, deferred (`tasks/deferred.md` D4, D5)**
  - [-] RAG suites seeded and reviewed; first measured baselines recorded — **suite seeded and smoke-measured; review and baselines skipped, deferred (`tasks/deferred.md` D1, D2)**

#### TASK-037: Faithfulness judge calibration
- **Depends on:** TASK-019, TASK-015
- **Files:** `evals/datasets/judge.calibration.jsonl`, `backend/evals/judges.py`
- **Description:** Hand-label 30 RAG answers for faithfulness; compute Cohen's κ for `eval.judge.faithfulness` on the pinned judge model. If κ ≥ 0.7, promote `rag.faithfulness` from advisory to a regression gate.
- **Acceptance criteria:**
  - [x] κ recorded in `memory/ai.md`; gate status of `rag.faithfulness` matches it (κ 1.00 on 30 constructed labels; stays advisory until the first reviewed RAG run, `tasks/deferred.md` D1, D2)

#### TASK-020: File deletion
- **Depends on:** TASK-018
- **Files:** `backend/app/api/files.py`, `backend/app/jobs/handlers.py`
- **Description:** Delete Qdrant points by `file_id`, then the disk file, then the row; cancel in-flight ingestion.
- **Acceptance criteria:**
  - [ ] A deleted file is never returned by search (tested)

#### TASK-021: Files UI
- **Depends on:** TASK-017, TASK-013 · *parallel with TASK-018*
- **Files:** `frontend/src/features/files/*`, composer attach button, citation chips
- **Description:** Upload with progress, status polling, files page, citation chips with snippet popovers.
- **Acceptance criteria:**
  - [ ] Playwright `rag.spec.ts` (fake LLM): upload → ready → cited answer

## M4 — Tools + HITL  [008-tools-hitl]

#### TASK-022: Calculator & web search tools
- **Depends on:** TASK-019
- **Files:** `backend/app/graph/tools/{calculator,web_search}.py`
- **Description:** A safe AST-based calculator (no eval); Tavily search with a 10 s timeout, a cap of 5 results and domain metadata; results wrapped as untrusted. Add the tool-routing section to `nexus.system` (doc 08 §4).
- **Acceptance criteria:**
  - [x] Calculator fuzz tests show no code execution
  - [-] Tool-routing suite seeded and reviewed; first measured baselines recorded — **seeded and wired, not reviewed, not measured on a real model; skipped, deferred (`tasks/deferred.md` D11; D10 for the `nexus.system` v3 smoke run)**

#### TASK-023: Interrupt / resume flow
- **Depends on:** TASK-022, TASK-011
- **Files:** `backend/app/graph/nodes/approval.py`, `backend/app/graph/policy.py` (`TOOL_POLICY`), `backend/app/api/chat.py` (resume), `frontend/src/features/chat/ApprovalCard.tsx`
- **Description:** `interrupt()` before every `web_search` (per-call approval in v1); when a message mixes calls, interrupt first and run all of them together after the decision (doc 08 §2.2); `pending_interrupt` persisted on the conversation (no run lease while waiting); resume with approve/edit/reject; 24 h expiry; card restored on reload.
- **Acceptance criteria:**
  - [x] Playwright `tools.spec.ts` (fake LLM): approve, edit and reject paths, plus reload mid-approval
  - [x] Unit test (fake LLM): one message with `[search_my_files, web_search]` → no call runs before the decision; on reject every `tool_call_id` still gets a ToolMessage
  - [x] `tools.approval_enforced = 1` (absolute gate)

#### TASK-024: Tool-call UI
- **Depends on:** TASK-013
- **Files:** `frontend/src/features/chat/ToolCard.tsx`
- **Description:** Render `tool_start`/`tool_end` with name, args and status; stored `tool_calls` shown in history.
- **Acceptance criteria:**
  - [ ] Component tests for every status

## M5 — Long-term memory  [009-long-term-memory]

#### TASK-025: Memory store & extraction job
- **Depends on:** TASK-012, TASK-010
- **Files:** `backend/app/memory/{store,extract}.py`, `backend/app/jobs/handlers.py`
- **Description:** `AsyncPostgresStore` with namespace `("memories", user_id)` and embedding index; `extract_memories` job after each turn (background model, effort `none`, strict structured output: create/update/delete ops); deny-list of sensitive categories; instruction-like content rejected; a `create` op with similarity ≥ 0.9 to an existing memory is dropped. Explicit "remember X" requests are handled here in v1.
- **Acceptance criteria:**
  - [ ] `memory.sensitive_refusal = 1` (absolute gate); extraction suite baselines recorded
  - [ ] Nothing is stored when memory is disabled

#### TASK-026: Memory in context
- **Depends on:** TASK-025, TASK-023
- **Files:** `backend/app/graph/nodes/load_context.py`, `backend/prompts/nexus.context.md`, `backend/prompts/nexus.system.md`
- **Description:** Inject the top-k relevant memories in the late context message as untrusted user-profile data. There is no memory-writing tool in `general_chat` (revision 1.3 removed `remember_fact` and its taint rule); `nexus.system` says memories are saved automatically and deleted in Settings → Memory, and never to claim otherwise.
- **Acceptance criteria:**
  - [x] "Remember that I prefer Go" leads to exactly one Go memory after the extraction job (`memory.e2e`)
  - [x] rt-05: a web result asking to remember something never produces a memory *(deterministic, fake-model test; the real-model record rides with TASK-029, `tasks/deferred.md` D17/D13)*
  - [x] Isolation test passes

#### TASK-027: Memories API & UI
- **Depends on:** TASK-025, TASK-007
- **Files:** `backend/app/api/memories.py`, `frontend/src/features/settings/Memory.tsx`
- **Description:** CRUD endpoints plus clear-all; settings page with toggle, inline edit and delete; toast after extraction.
- **Acceptance criteria:**
  - [x] Playwright `memory.spec.ts` passes

## M6 — Hardening  [010-hardening]

#### TASK-028: Account deletion & purge
- **Depends on:** TASK-020, TASK-025
- **Files:** `backend/app/api/me.py`, `backend/app/jobs/handlers.py` (`purge_user`)
- **Description:** Deletion revokes sessions and enqueues a purge across Postgres, checkpoints, store, Qdrant and disk. (Data export is deferred to after v1.)
- **Acceptance criteria:**
  - [x] Cross-store purge test verifies zero rows, checkpoints, store items, points and files remain for the user *(the Settings → Account UI is deferred, `tasks/deferred.md` D18)*

#### TASK-029: Red-team & safety evals
- **Depends on:** TASK-015, TASK-023, TASK-026
- **Files:** `evals/datasets/safety.redteam.jsonl`, `backend/evals/canaries.py`
- **Description:** Implement the doc 09 §8 red-team set (40 cases: injection via documents, web results and memories; exfiltration; cross-tenant probing) scored by deterministic canaries, and fix any failures.
- **Acceptance criteria:**
  - [-] Injection resistance ≥ 0.95 (absolute gate) — **built and tested offline, gate recorded at its target, not measured on a real model; skipped, deferred (`tasks/deferred.md` D19)**
  - [-] Cross-user leakage = 0 — **same: metric and gate (0) built, not measured on a real model (D19)**

#### TASK-030: Security review
- **Depends on:** TASK-028
- **Files:** `deploy/Caddyfile`, `backend/app/core/*`
- **Description:** Walk the doc 10 threat table; verify CSP and headers, upload limits, invite handling, dependency audit, trace redaction and secrets handling; fix findings.
- **Acceptance criteria:**
  - [ ] Every LLM-Txx threat has a mitigation and a test reference

#### TASK-031: Load & resilience test
- **Depends on:** TASK-023
- **Files:** `loadtest/locustfile.py`
- **Description:** 20 concurrent streams + 5 rps against `LLM_MODE=fake` (with realistic token delays). Inject model failures through the fake model to verify the fallback; restart the API mid-load to verify drain and reaping. No real-model load run.
- **Acceptance criteria:**
  - [ ] Non-model latency overhead within NFR targets; no errors under the target load
  - [ ] No conversation stays locked after a restart under load

#### TASK-032: Coverage & observability polish
- **Depends on:** all M2–M5 tasks
- **Files:** `tests/coverage-baseline.json`
- **Description:** Close coverage gaps to the 80% / 70% targets (ADR-0009); Langfuse dashboards for cost, latency and feedback. (Prometheus metrics deferred.)
- **Acceptance criteria:**
  - [ ] Every package meets its coverage target and the baseline file records it

#### TASK-035: Release checklist
- **Depends on:** all
- **Files:** `docs/02_outputs/release_checklist.md`, `README.md`
- **Description:** Final full eval run, cost review against G4, privacy section in the README (data stored, Langfuse Cloud EU, OpenAI `store=false`, retention, deletion), admin CLI documented, fresh-clone timing (G8).
- **Acceptance criteria:**
  - [ ] `make eval` (full) passes on the prod config
  - [ ] A fresh clone reaches a working stack in ≤ 15 min following the README

---

## M7 — Intent routing & generated outputs (v1.1)

Starts after M6 so the v1 core ships hardened first. Every task ships with its eval suite (G9); safety gates are added before the feature is enabled in prod.

### Router, slash commands & memory by chat  [011-intent-router]

#### TASK-038: Router node, dispatch & route events
- **Depends on:** TASK-035
- **Files:** `backend/app/graph/router.py`, `backend/app/graph/commands.py` (slash-command parser), `backend/app/graph/builder.py`, `backend/prompts/nexus.route.md`, `backend/app/llm/factory.py` (`router` role), `backend/app/api/chat.py` (`/help`, usage replies), `adr/0010-intent-router.md`
- **Description:** `RouteDecision` structured output on `gpt-6-luna` over 4 intents, running in parallel with `load_context`; dispatch to subgraphs with `general_chat` as fallback (confidence below `min_confidence` from the `nexus.route.md` front-matter, error, 5 s timeout); router input = last 3 user messages + previous intent only; deterministic slash-command parser (doc 08 §2.6) that skips the router, with `/help` answered without a model and usage replies for malformed commands; resume never re-routes; `route_selected` SSE event (`source=router|command|fallback`); `intent`/`route_source` stored on messages; trace tags; kill switches `ROUTER_DISABLED`, `INTENTS_DISABLED` (runtime flags); `nexus.system` gains the "never offer a PDF or research yourself" rule. Fake-LLM mode supports scripted route decisions.
- **Acceptance criteria:**
  - [ ] Unit tests (fake LLM): fallback on low confidence, timeout and invalid JSON; slash-command bypass; resume keeps the subgraph
  - [ ] Parser unit tests: every command in doc 08 §2.6, case-insensitivity, commands only at the start, malformed commands → usage reply
  - [ ] `min_confidence` tuned on the first reviewed full run (best `router.intent_accuracy`, ties → higher) or dropped if confidence is degenerate (doc 08 §2.2); result in `memory/ai.md`
  - [ ] `ops.ttft_p95_ms` measured end to end with the router; if it misses, the keyword pre-gate is added
  - [ ] `router.intents` dataset (120 / 24, 4 intents) reviewed; first baselines for `router.intent_accuracy`, `router.macro_f1`, `router.slot_accuracy`, `router.latency_p95_ms` recorded
  - [ ] rt-22 passes; router input asserted to contain user text only

#### TASK-039: Memory by chat
- **Depends on:** TASK-038, TASK-026
- **Files:** `backend/app/graph/intents/manage_memory.py`, `backend/app/memory/ops.py`, `frontend/src/features/chat/ApprovalCard.tsx` (candidate picker)
- **Description:** `manage_memory` `list` (template, no LLM), `save` (deny-list + instruction filter) and `forget` (top 3, similarity ≥ 0.75; `forget_memory` interrupt with 1 clear match or up to 3 candidates; delete exactly one on approve); `extract_memories` skipped on these turns; memory off → short notice.
- **Acceptance criteria:**
  - [ ] `memory.e2e` cases pass through `manage_memory`, including the one-clear-match and ambiguous forget cases (doc 09 §9)
  - [ ] Every forget interrupts before deleting (`tools.approval_enforced`); reject and expiry delete nothing

#### TASK-040: Mode chip & slash commands (UI)
- **Depends on:** TASK-038
- **Files:** `frontend/src/features/chat/{ModeChip,SlashMenu}.tsx`, `frontend/src/api/stream.ts`
- **Description:** Render `route_selected` as a chip on the answer; the `/` menu inserts command text (the backend parses it); hide Regenerate on non-chat answers.
- **Acceptance criteria:**
  - [ ] Component tests; Playwright (fake LLM): `/pdf` produces a Document chip and a document card; Regenerate is hidden on it

### Documents & research  [012-documents-research]

#### TASK-041: Document pipeline & PDF rendering
- **Depends on:** TASK-038
- **Files:** `backend/app/graph/intents/generate_document.py`, `backend/app/documents/{draft,render,fetcher}.py`, `backend/app/jobs/render_document.py`, `backend/prompts/nexus.document.md`, migration (`documents`), `backend/Dockerfile` (WeasyPrint system libs)
- **Description:** Gather (`thread`: checkpoint summary + recent messages ≤ 16k tokens; `last_answer`: the previous assistant message, converted with no LLM call; `topic`: one approved `web_search`) → `DocumentDraft` on `long_form` (max output 4k) → persist → `render_document` job (sanitised HTML, deny-all URL fetcher, 60 s, ≤ 30 pages) → `document_created`; worst-case cost pre-check against the user budget and the global cap; limits 10/day, keep 100; `cleanup_documents`; cancel semantics (doc 08 §2.3). Extend `purge_user` to delete `documents` rows and PDF files.
- **Acceptance criteria:**
  - [ ] `documents.golden` reviewed; `documents.render_success` baseline recorded; `documents.external_fetches = 0` (absolute)
  - [ ] rt-23 passes; the fetcher unit test rejects `file://`, `http(s)://` and `data:` outside the allowlist
  - [ ] The cross-store purge test also checks that no `documents` rows or PDF files remain for the user
  - [ ] `ops.cost_per_document_run_usd` baseline recorded; worst case ≤ $0.05 by construction

#### TASK-042: Documents API & UI
- **Depends on:** TASK-041
- **Files:** `backend/app/api/documents.py`, `frontend/src/features/documents/*`, `frontend/src/features/chat/DocumentCard.tsx`
- **Description:** List/get/delete/download endpoints (owner-scoped, 404 on mismatch); download through a Bearer fetch → blob; `/documents` page; document card with polling.
- **Acceptance criteria:**
  - [ ] Contract tests for the new paths; IDOR test (user B gets 404 on user A's document and download)

#### TASK-043: Deep research
- **Depends on:** TASK-041, TASK-023
- **Files:** `backend/app/graph/intents/deep_research.py`, `backend/prompts/{nexus.research_plan,nexus.research_report}.md`, `frontend/src/features/chat/ResearchPlanCard.tsx`, `evals/fixtures/web/`
- **Description:** Plan (max output 1k) → `research_plan` interrupt (editable queries) → parallel approved searches (≤ 16k tokens of results) with `progress` events → report (`long_form`, max output 4k) streamed to chat, then rendered through the `last_answer` path when a PDF was asked for; worst-case pre-check against the user budget and the global cap; cancel semantics (doc 08 §2.3). Eval runner replays recorded Tavily fixtures.
- **Acceptance criteria:**
  - [ ] `research.unapproved_queries = 0` and `tools.approval_enforced = 1` (absolute)
  - [ ] `research.golden` reviewed; `ops.cost_per_research_run_usd` baseline ≤ $0.05; rt-25 passes

#### TASK-046: M7 hardening & release
- **Depends on:** TASK-038…043
- **Files:** `evals/eval-baseline.json`, `docs/02_outputs/release_checklist.md`
- **Description:** Full eval run incl. rt-22, rt-23, rt-25; review the router confusion matrix and add misroutes to `router.intents`; cost review (G4 excludes research and document runs, which are checked against their caps); kill switches tested in prod config.
- **Acceptance criteria:**
  - [ ] `make eval` (full) passes with all v1.1 gates present in `metrics`

---

## Dependency Graph Summary

```mermaid
flowchart LR
  M1[M1 Skeleton, live<br/>004] --> CC[M2 Core chat<br/>005]
  CC --> EV[M2 Eval harness<br/>006]
  EV --> R[M3 RAG<br/>007]
  R --> T[M4 Tools/HITL<br/>008]
  T --> MEM[M5 Memory<br/>009]
  MEM --> H[M6 Hardening<br/>010]
  H --> RO[M7 Router + fast paths<br/>011]
  RO --> DOC[M7 Documents + research<br/>012]
```

- **Sequential:** milestones run in order; each ends deployed. The eval harness lands in M2 so every later feature ships with its suite and gate (G9).
- **Parallel inside a milestone:** frontend tasks run alongside their backend counterparts once the API spec is stable; TASK-010 and TASK-012 can run in parallel.
- **Order changes from v1.0:** deploy and backups moved from the last phase into M1; memory follows tools (first because `remember_fact`'s approval rule built on the interrupt flow; revision 1.3 removed that tool, and the order stays because M7's forget confirmation reuses the same flow).
