# Implementation Plan — Nexus AI v1

**Version:** 1.2 · **Date:** 2026-11-21 (M7 added: intent routing & generated outputs) · previous 1.1 2026-11-19 (task 003) · **Inputs:** [PRD](01_prd.md), [Architecture](03_tech_architecture.md), [API](04_api_spec.yaml), [AI design](08_ai_system_design.md), [Eval plan](09_eval_plan.md), [Threat model](10_llm_threat_model.md)

Work is organised by **milestone** (PRD §12). Milestones set the order, not dates; the builder is one developer working part-time with AI coding agents. **Every milestone ends deployed**: run the `deploy.yml` workflow and check `/readyz` and a chat smoke on the hosted instance.

Each repo task group in brackets becomes `tasks/active/<NNN>-<slug>.md` when started (task 003 was the plan revision, so groups start at 004). TASK ids are stable references; tasks added in revision 1.1 are numbered from 036, and M7 tasks (revision 1.2) from 038.

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
- **Description:** Register (invite code required when `REGISTRATION_MODE=invite`, the default), login, refresh (rotation + family revocation), logout; argon2id hashing; JWT (HS256, 15 min); `/me` GET/PATCH, password change; auth and invite-attempt rate limits. Admin CLI: `invite create|list|revoke`, `reset-password`, `disable-user`/`enable-user`.
- **Acceptance criteria:**
  - [ ] Refresh-token reuse revokes the whole family (tested)
  - [ ] Login does not reveal whether an email exists
  - [ ] Unknown, used, revoked and expired invite codes all return the same `INVITE_INVALID` (tested)
  - [ ] Contract tests for the auth endpoints pass

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
- **Description:** `get_model(role)` per doc 08 §3: reasoning effort per role (temperature only at effort `none`), Responses API with `store=false`, timeouts, retries, fallback and the Langfuse callback (SDK v5; enable per-node spans). `LLM_MODE=fake` returns a scripted model (text, tool calls, interrupts; per-token delay) keyed by fixture id. Prompts load from `backend/prompts/` on disk (ADR-0008); `sync_prompts.py` pushes changed prompts to Langfuse in CI. HMAC user hashing; `mask` redaction; pricing table including cached and reasoning tokens.
- **Acceptance criteria:**
  - [ ] Unit tests with the fake model; the factory never sends `temperature` with an effort other than `none`
  - [ ] `mask` unit tests cover emails, phone numbers, card/IBAN-like numbers, JWTs and API keys
  - [ ] Verify whether multi-turn tool calls with `store=false` need encrypted reasoning items; record the answer in `memory/ai.md`
- > **ADR:** ADR-0005, ADR-0008

#### TASK-010: Chat graph v1
- **Depends on:** TASK-005, TASK-009
- **Files:** `backend/app/graph/{state,builder}.py`, `backend/app/graph/nodes/{load_context,agent,summarize}.py`
- **Description:** The LangGraph graph from doc 08 without tools; `AsyncPostgresSaver`; trimming and summarisation (summary lives in graph state only); cache-friendly prompt layout (doc 08 §4: fixed system prompt → summary → history → late context message → newest user message).
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
- **Description:** Postgres SKIP LOCKED queue with retries, backoff, idempotency keys and graceful shutdown; `generate_title` handler; periodic `reap_stale_runs` (30 s) and `cleanup_invites` (daily).
- **Acceptance criteria:**
  - [ ] Two workers never process the same job (concurrency test)
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
- **Description:** Run the graph against JSONL datasets (synced to Langfuse datasets), score with metrics and judges from doc 09, write `evals/results/latest.json`, and log runs to Langfuse. `--suite smoke|full`. AI agents draft dataset records; the developer reviews them. Record the first measured values as regression gates (ADR-0009); judged metrics stay advisory.
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
- **Description:** `search_my_files` using Qdrant `query_points` with a mandatory `user_id` filter; returns numbered passages wrapped as untrusted data; emits citation events; marks the turn as having read untrusted content (used by the `remember_fact` policy in M5).
- **Acceptance criteria:**
  - [ ] Isolation test: user B never retrieves user A's chunks
  - [ ] RAG suites seeded and reviewed; first measured baselines recorded

#### TASK-037: Faithfulness judge calibration
- **Depends on:** TASK-019, TASK-015
- **Files:** `evals/datasets/judge.calibration.jsonl`, `backend/evals/judges.py`
- **Description:** Hand-label 30 RAG answers for faithfulness; compute Cohen's κ for `eval.judge.faithfulness` on the pinned judge model. If κ ≥ 0.7, promote `rag.faithfulness` from advisory to a regression gate.
- **Acceptance criteria:**
  - [ ] κ recorded in `memory/ai.md`; gate status of `rag.faithfulness` matches it

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
- **Description:** A safe AST-based calculator (no eval); Tavily search with a 10 s timeout, a cap of 5 results and domain metadata; results wrapped as untrusted (and mark the turn as having read untrusted content).
- **Acceptance criteria:**
  - [ ] Calculator fuzz tests show no code execution
  - [ ] Tool-routing suite seeded and reviewed; first measured baselines recorded

#### TASK-023: Interrupt / resume flow
- **Depends on:** TASK-022, TASK-011
- **Files:** `backend/app/graph/nodes/approval.py`, `backend/app/graph/policy.py` (`TOOL_POLICY`), `backend/app/api/chat.py` (resume), `frontend/src/features/chat/ApprovalCard.tsx`
- **Description:** `interrupt()` before every `web_search` (per-call approval in v1); `TOOL_POLICY` supports conditional rules (used by `remember_fact` in M5); `pending_interrupt` persisted on the conversation (no run lease while waiting); resume with approve/edit/reject; 24 h expiry; card restored on reload.
- **Acceptance criteria:**
  - [ ] Playwright `tools.spec.ts` (fake LLM): approve, edit and reject paths, plus reload mid-approval
  - [ ] `tools.approval_enforced = 1` (absolute gate)

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
- **Description:** `AsyncPostgresStore` with namespace `("memories", user_id)` and embedding index; `extract_memories` job after each turn (background model, effort `none`, strict structured output: create/update/delete ops); deny-list of sensitive categories.
- **Acceptance criteria:**
  - [ ] `memory.sensitive_refusal = 1` (absolute gate); extraction suite baselines recorded
  - [ ] Nothing is stored when memory is disabled

#### TASK-026: Memory in context & remember_fact tool
- **Depends on:** TASK-025, TASK-023
- **Files:** `backend/app/graph/nodes/load_context.py`, `backend/app/graph/tools/remember_fact.py`, `backend/app/graph/policy.py`
- **Description:** Inject the top-k relevant memories in the late context message as untrusted user-profile data; tool for explicit "remember / forget" requests; **taint rule**: if the turn already read untrusted content, `remember_fact` goes through the approval interrupt; `memory_updated` event.
- **Acceptance criteria:**
  - [ ] "Forget X" removes X (eval case)
  - [ ] A `remember_fact` call after a web or file result always interrupts (unit + red-team case rt-05)
  - [ ] Isolation test passes

#### TASK-027: Memories API & UI
- **Depends on:** TASK-025, TASK-007
- **Files:** `backend/app/api/memories.py`, `frontend/src/features/settings/Memory.tsx`
- **Description:** CRUD endpoints plus clear-all; settings page with toggle, inline edit and delete; toast after extraction.
- **Acceptance criteria:**
  - [ ] Playwright `memory.spec.ts` passes

## M6 — Hardening  [010-hardening]

#### TASK-028: Account deletion & purge
- **Depends on:** TASK-020, TASK-025
- **Files:** `backend/app/api/me.py`, `backend/app/jobs/handlers.py` (`purge_user`)
- **Description:** Deletion revokes sessions and enqueues a purge across Postgres, checkpoints, store, Qdrant and disk. (Data export is deferred to after v1.)
- **Acceptance criteria:**
  - [ ] Cross-store purge test verifies zero rows, checkpoints, store items, points and files remain for the user

#### TASK-029: Red-team & safety evals
- **Depends on:** TASK-015, TASK-023, TASK-026
- **Files:** `evals/datasets/safety.redteam.jsonl`, `backend/evals/canaries.py`
- **Description:** Implement the doc 09 §8 red-team set (40 cases: injection via documents, web results and memories; exfiltration; cross-tenant probing) scored by deterministic canaries, and fix any failures.
- **Acceptance criteria:**
  - [ ] Injection resistance ≥ 0.95 (absolute gate)
  - [ ] Cross-user leakage = 0

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

### Router & cheap intents  [011-intent-router]

#### TASK-038: Router node, dispatch & route events
- **Depends on:** TASK-035
- **Files:** `backend/app/graph/router.py`, `backend/app/graph/builder.py`, `backend/prompts/nexus.route.md`, `backend/app/llm/factory.py` (`router` role), `backend/app/api/chat.py` (`mode`, `client_timezone`), `adr/0010-intent-router.md`
- **Description:** `RouteDecision` structured output on `gpt-6-luna`, running in parallel with `load_context`; dispatch to subgraphs with `general_chat` as fallback (confidence < 0.70, error, 5 s timeout); `mode` skips the router; resume never re-routes; `route_selected` SSE event; `intent`/`route_source` stored on messages; trace tags; kill switches `ROUTER_DISABLED`, `INTENTS_DISABLED`. Fake-LLM mode supports scripted route decisions.
- **Acceptance criteria:**
  - [ ] Unit tests (fake LLM): fallback on low confidence, timeout and invalid JSON; `mode` bypass; resume keeps the subgraph
  - [ ] `router.intents` dataset (120 / 24) reviewed; first baselines for `router.intent_accuracy`, `router.macro_f1`, `router.slot_accuracy`, `router.latency_p95_ms` recorded
  - [ ] rt-22 passes; router input asserted to contain user text only

#### TASK-039: Fast path & memory by chat
- **Depends on:** TASK-038, TASK-026
- **Files:** `backend/app/graph/intents/{smalltalk_help,manage_memory}.py`, `backend/prompts/nexus.smalltalk.md`, `backend/app/memory/ops.py` (shared with `remember_fact`)
- **Description:** `smalltalk_help` on the router model tier with a static capability list; `manage_memory` list/save/forget reusing the GAP-08 resolution rules, with no LLM call for `list`.
- **Acceptance criteria:**
  - [ ] `memory.e2e` cases pass through the `manage_memory` route as well as `remember_fact`
  - [ ] `ops.ttft_p95_ms` measured separately for `smalltalk_help` (expected well below the chat path)

#### TASK-040: Mode chip & slash commands (UI)
- **Depends on:** TASK-038
- **Files:** `frontend/src/features/chat/{ModeChip,SlashMenu}.tsx`, `frontend/src/api/stream.ts`
- **Description:** Render `route_selected` as a chip on the answer; the `/` menu sets `mode`; send `client_timezone`.
- **Acceptance criteria:**
  - [ ] Component tests; Playwright: `/pdf` sends `mode=document`

### Documents & research  [012-documents-research]

#### TASK-041: Document pipeline & PDF rendering
- **Depends on:** TASK-038
- **Files:** `backend/app/graph/intents/generate_document.py`, `backend/app/documents/{draft,render,fetcher}.py`, `backend/app/jobs/render_document.py`, `backend/prompts/nexus.document.md`, migration (`documents`), `backend/Dockerfile` (WeasyPrint system libs)
- **Description:** Gather (thread from the read model, or one approved `web_search`) → `DocumentDraft` on `long_form` → persist → `render_document` job (sanitised HTML, deny-all URL fetcher, 60 s, ≤ 30 pages) → `document_created`; limits 10/day, keep 100; `cleanup_documents`.
- **Acceptance criteria:**
  - [ ] `documents.golden` reviewed; `documents.render_success` baseline recorded; `documents.external_fetches = 0` (absolute)
  - [ ] rt-23 and rt-24 pass; the fetcher unit test rejects `file://`, `http(s)://` and `data:` outside the allowlist

#### TASK-042: Documents API & UI
- **Depends on:** TASK-041
- **Files:** `backend/app/api/documents.py`, `frontend/src/features/documents/*`, `frontend/src/features/chat/DocumentCard.tsx`
- **Description:** List/get/delete/download endpoints (owner-scoped, 404 on mismatch); download through a Bearer fetch → blob; `/documents` page; document card with polling.
- **Acceptance criteria:**
  - [ ] Contract tests for the new paths; IDOR test (user B gets 404 on user A's document and download)

#### TASK-043: Deep research
- **Depends on:** TASK-041, TASK-023
- **Files:** `backend/app/graph/intents/deep_research.py`, `backend/prompts/{nexus.research_plan,nexus.research_report}.md`, `frontend/src/features/chat/ResearchPlanCard.tsx`, `evals/fixtures/web/`
- **Description:** Plan → `research_plan` interrupt (editable queries) → parallel approved searches with `progress` events → report (chat or PDF hand-off); budget pre-check and $0.05 cap. Eval runner replays recorded Tavily fixtures.
- **Acceptance criteria:**
  - [ ] `research.unapproved_queries = 0` and `tools.approval_enforced = 1` (absolute)
  - [ ] `research.golden` reviewed; `ops.cost_per_research_run_usd` baseline ≤ $0.05; rt-25 passes

### Schedules  [013-schedules]

#### TASK-044: Schedule intent, storage & worker runs
- **Depends on:** TASK-041, TASK-043, TASK-012
- **Files:** `backend/app/graph/intents/schedule_task.py`, `backend/app/schedules/*`, `backend/app/jobs/{run_due_schedules,run_schedule}.py`, migration (`schedules`, `conversations.kind/unread`)
- **Description:** Parse slots → `create_schedule` interrupt → insert schedule + conversation; `run_due_schedules` every 60 s; `run_schedule` builds input only from the stored spec (stored query only; no `general_chat` tools; no memory writes), checks the budget, uses the run lease, pauses after 3 failures; list/pause/resume/delete by chat; `SCHEDULES_DISABLED`.
- **Acceptance criteria:**
  - [ ] Unit tests: `next_run_at` across DST changes; a 6th active schedule is refused; budget-skip note; pause after 3 failures; lease-held retry
  - [ ] A scheduled-run test asserts the Tavily query equals the stored query; rt-24 and rt-26 pass

#### TASK-045: Schedules API & UI
- **Depends on:** TASK-044
- **Files:** `backend/app/api/schedules.py`, `frontend/src/features/settings/Schedules.tsx`, `frontend/src/features/chat/ScheduleApprovalCard.tsx`, sidebar unread dot
- **Description:** List/pause/resume/delete endpoints; Settings → Schedules; approval card with editable fields; unread indicator.
- **Acceptance criteria:**
  - [ ] Contract + IDOR tests; Playwright: create (approve), pause, delete

#### TASK-046: M7 hardening & release
- **Depends on:** TASK-038…045
- **Files:** `evals/eval-baseline.json`, `docs/02_outputs/release_checklist.md`
- **Description:** Full eval run incl. rt-22–26; review the router confusion matrix and add misroutes to `router.intents`; cost review (G4 excludes research/document/scheduled runs, which are checked against their caps); kill switches tested in prod config.
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
  DOC --> SC[M7 Schedules<br/>013]
```

- **Sequential:** milestones run in order; each ends deployed. The eval harness lands in M2 so every later feature ships with its suite and gate (G9).
- **Parallel inside a milestone:** frontend tasks run alongside their backend counterparts once the API spec is stable; TASK-010 and TASK-012 can run in parallel.
- **Order changes from v1.0:** deploy and backups moved from the last phase into M1; memory now follows tools because the `remember_fact` approval rule builds on the interrupt flow.
