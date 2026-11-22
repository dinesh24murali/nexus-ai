# backend/ — AGENTS.md

## Stack

- **Language:** `Python 3.14`
- **Framework:** `FastAPI`
- **Database:** `PostgreSQL 18` via psycopg 3 (`postgresql+psycopg://`) — the only Postgres driver
- **Auth:** `JWT (access token + rotating refresh cookie)`

---

## Project Structure

```
backend/
├── app/
│   ├── main.py             ← create_app() factory (run: uvicorn app.main:create_app --factory)
│   ├── api/                ← routers (thin), deps.py (auth + session factory), schemas.py,
│   │                         chat.py (send/regenerate/cancel/history) + sse.py (typed events, framing),
│   │                         feedback.py (PUT /messages/{id}/feedback), files.py (upload, list, get),
│   │                         memories.py (list, add, edit, delete, clear all);
│   │                         `Message.documents` is filled in chat.py (the /documents endpoints are TASK-042)
│   ├── services/           ← use cases and transaction boundaries (auth.py; files.py = upload checks, storage,
│   │                         duplicate/limit, enqueue `ingest_file`; runs.py = run lease,
│   │                         streaming, cancel, regenerate, drain, pre-run limits; usage.py = spend reads,
│   │                         budget check, /me/usage, spend alert; feedback.py = rate an answer + Langfuse score; memories.py = the
│   │                         memories API use cases, same policy as extraction; documents.py = the
│   │                         `generate_document` pipeline's service: day limit, worst-case budget
│   │                         pre-check, reads of the conversation, row + render job in one transaction)
│   ├── jobs/               ← queue.py (Postgres SKIP LOCKED), handlers.py (registry: kind -> handler,
│   │                         timeout, attempts; `render_document` too), periodic.py (reaper, stale-job
│   │                         requeue, cleanups incl. `cleanup_documents`)
│   ├── db/                 ← base, models, session, repositories/ (every query is owner-scoped)
│   ├── core/               ← config, logging, errors, security, ratelimit, budgets (policy), runtime_flags, health, net
│   ├── cli.py              ← admin CLI (python -m app.cli); worker.py (job loops + periodic); migrate.py
│   ├── llm/                ← factory.py (the only place provider clients are built), fake.py (LLM_MODE=fake),
│   │                         prompts.py + prompt_sync.py, tracing.py, redaction.py, pricing.py, tokens.py,
│   │                         moderation.py (input moderation, fail-open),
│   │                         usage.py (per-run token + cost collector)
│   ├── graph/              ← state.py, prompting.py (cache-friendly request layout), builder.py, policy.py (TOOL_POLICY),
│   │                         calls.py, router.py (intent router: RouteDecision, input = user text only, decide + fallbacks),
│   │                         commands.py (slash-command parser, pure), intents/ (PIPELINES; manage_memory = list/save/forget
│   │                         by chat, forget_confirm = the card), nodes/ (agent, load_context, summarize, approval = interrupt
│   │                         per call that needs it, tools = the tool loop + cap), tools/ (search_my_files, web_search,
│   │                         calculator, outcome); intents/generate_document.py (check, gather, search,
│   │                         compose, persist)
│   ├── documents/          ← PDF documents: draft.py (`DocumentDraft`, limits, `draft_from_answer`, material
│   │                         for the model), markup.py (untrusted Markdown -> sanitised HTML), fetcher.py (deny-all
│   │                         URL fetcher, counts refusals), render.py + worker_main.py (WeasyPrint in a killable
│   │                         child process), storage.py (`<UPLOADS_DIR>/<user>/documents/<id>.pdf`), assets/
│   ├── rag/                ← formats.py, parsers.py (PDF/DOCX/TXT/MD), chunker.py (800/120 tokens), embedder.py,
│   │                         retriever.py (query -> tenant-filtered passages),
│   │                         index.py (Qdrant: alias `documents`, tenant payload); ingestion job is in jobs/handlers.py
│   └── memory/             ← ops.py (memory by chat: wording, category, forget candidates, reply templates; pure), manage.py
│                             (`MemoryManager` for `manage_memory`), store.py (per-user `AsyncPostgresStore`, namespace built from the user id), policy.py (what may be
│                             remembered: deny-list + instruction filter, pure), extract.py (plan_ops / apply_plan), read.py (what
│                             `load_context` shows: select_for_prompt, memory_lookup_for; touch marks use)
├── evals/                  ← the eval suite (`python -m evals.run`; judge calibration `python -m evals.calibrate`;
│                             see evals/AGENTS.md); not imported by app/
├── prompts/                ← canonical prompt files `<name>.md` (front-matter name + version; ADR-0008)
├── alembic/                ← async env, forward-only migrations in versions/
├── tests/                  ← unit + integration (marker `integration`), contract/ (schemathesis)
├── Dockerfile, uvicorn_logging.json
└── pyproject.toml, uv.lock
```

---

## Conventions

### API Handlers
- Handlers only parse/validate input (pydantic schemas in `api/schemas.py`) and call a service; routers stay thin
- Services own transactions and business rules; repositories hold queries and never commit
- Authenticate with the `CurrentUserDep` dependency and pass `current.id` into every repository call. Another user's resource is a **404**, never 403
- The OpenAPI spec is the contract: schemas mirror it exactly (same nullability, same length limits). Do not be stricter than the spec; if you must be, change the spec first

### Error Handling
- Raise `ApiError(status, CODE, message, details=..., headers=...)` anywhere in a request; the handler emits `{"error": {code, message, details?, request_id}}`
- Bad bodies of every kind are `422 VALIDATION_FAILED` and never echo submitted values (passwords, invite codes)
- A use case that must persist something and then fail (record a rate-limit hit, revoke a token family) finishes its transaction first and raises afterwards; raising inside `session.begin()` rolls the write back
- Auth failures must not reveal why: `INVITE_INVALID` is identical for unknown/used/revoked/expired; login never reveals whether an email exists

### Database Access
- SQLAlchemy 2 async over psycopg 3. Models in `db/models.py`; enums are `text` + CHECK constraints
- Migrations: `uv run alembic revision --autogenerate -m "..."`, then **review and hand-edit** (extensions, modern typing, indexes). `tests/db/test_migrations.py` fails if models and migrations drift
- Concurrency-sensitive reads use `SELECT ... FOR UPDATE` (invite redemption, refresh-token rotation)
- LangGraph's own tables are created by `python -m app.migrate`, not by Alembic

### Chat runs and jobs
- A run is a detached asyncio task (`services/runs.py`): the HTTP response only drains its event queue, so a client disconnect never stops it; only `POST …/runs/cancel` (or deleting the conversation) does. One atomic UPDATE takes the lease; ending a run is one transaction that also checks the lease is still ours (if the reaper took it, the run writes nothing)
- Everything a run or job persists for a user goes through owner-scoped repositories; the job's `user_id` column (not its payload) says whose job it is
- Jobs are idempotent (the queue can run one twice after a crash) and are enqueued in the caller's transaction with an idempotency key. New job kinds register in `jobs/handlers.py::JOBS` with a hard timeout and attempt limit
- Anything that waits for time (heartbeats, stale windows, drain) takes its duration as a parameter so tests run it scaled down; production constants live next to the code (`periodic.py`, `worker.py`, `runs.py`)

### Tools and approvals
- A tool needs an entry in `graph/policy.py::TOOL_POLICY` (`always` = every call waits for the user; a test fails without the entry), a `ToolOutcome`-returning runner in `graph/tools/`, and a row in `docs/02_outputs/10_llm_threat_model.md`
- Two locks guard an approval tool: the `approval` node interrupts before any call of the message runs, and the `tools` node runs such a call only with a recorded decision. A decision that is not a clear `approve` or a valid `edit` is a rejection. Every `tool_call_id` ends with a ToolMessage
- A paused run holds no lease: `conversations.pending_interrupt` keeps the approval (24 h) and the checkpoint keeps the thread. `RunService.start_resume` takes the lease and clears the approval in one UPDATE, so a double click resumes once; the resumed run is not a "message sent" in `usage_daily`. A new message abandons a pending approval, and `_repair_thread` answers any tool call the thread left open before the model sees it again
- `TOOLS_DISABLED` reaches the graph as `config["configurable"]["disabled_tools"]` per run (the graph is built once), so it also stops an approval that was already waiting
- Tool failures become results the model can read and never carry exception text, URLs or keys. Web results are escaped data (`<web_result>`); only `http(s)` pages are kept

### Memory (TASK-025)
- Every `MemoryStore` method takes the user id and builds the namespace itself; never pass a namespace or a key from model output. Text in a memory is user data: never log it, never put it in a prompt outside a data tag
- Anything that writes a memory goes through `memory.policy.check` (sensitive categories, instruction-like text, length) whoever proposed it, and a memory op may only name a memory that was shown to the model (`filter_ops`). The model is never trusted to obey its prompt
- `extract_memories` re-checks `memory_enabled` and `MEMORY_WRITES_DISABLED` when it runs, and is idempotent (a re-run finds its own creates as duplicates). The store needs a database URL: tests that run a worker with the default job registry must give it the test database (`settings.model_copy(update={"database_url": db_url})`), or the memory job would use the developer's database

### Memory in context (TASK-026)
- `load_context` gets memories from the injected `MemoryLookup`; the lookup returns nothing when the user has memory off and never fails the run. Memories reach the model only as escaped lines inside `<user_memory>` (`prompting._memory_lines`), never in the system prompt (cache prefix) and never in a log or trace field of their own
- Marking a memory as used is `MemoryStore.touch` (one `UPDATE`), never `update`/`aput`: those re-embed, move `updated_at` and can resurrect a deleted memory

### Account deletion (TASK-028)
- `DELETE /me` (`services/accounts.py`) only schedules: one transaction sets `pending_deletion`, revokes every refresh token, cancels the user's queued jobs and queues `purge_user`. The erasure is the job (`jobs/handlers.py::purge_user`): external stores first (Qdrant by `user_id`, memory store, the user's uploads directory), then one Postgres transaction that ends with the `users` row (FK cascade does the rest). Every step is idempotent, the job waits while another job of the user is running, and it refuses a user who is not `pending_deletion`
- A new table with a `user_id` needs `ON DELETE CASCADE` (or an explicit step in the purge): `tests/jobs/test_purge_user.py` finds user-owned tables from the catalogue and fails if any row of the user survives. A new store that holds user data (a vector collection, a directory) needs its own purge step and a line in that test

### Documents (TASK-041)
- `generate_document` reads and writes through the injected `DocumentService` (`services/documents.py`); the user id and `assistant_message_id` come from the trusted run config. Every node that can end the turn early returns a fixed chat reply (`document_stop`) and writes nothing; `document_check` resets the per-run `document_*` state keys because the checkpoint keeps them. The model's own `sources` are discarded in code. A brief's search is the chat approval card, and the node that interrupts has no side effect before `interrupt()`
- A PDF is built from untrusted text, so every layer is tested and mutation-checked: `markup.py` (raw HTML escaped, no images, `http(s)` links only and printed in full, `nh3` last), `fetcher.py` (one bundled stylesheet, everything else refused and counted), `render.py` (child process with a minimal environment, killed at 50 s, <= 30 pages, `.part` then rename; ADR-0011). Anything new the renderer should load goes into `documents/bundled.py::BUNDLED` and its test, never into the page
- `render_document` is idempotent, owner-scoped (`job.user_id`), and ends in a fixed reason (`render.REASON_*`) on failure; PDFs live under the user's own directory so `purge_user` removes them. Limits are settings (`DOCUMENTS_PER_DAY` 10, `DOCUMENTS_KEEP` 100). `RunService(pipelines=...)` must list what its graph can run
- Document tests need no mocks: the real renderer runs in the unit tests (about one second each) and the service and job tests use the real Postgres

### Limits and moderation
- A chat send/regenerate passes, in order: drain check, `ratelimit.hit` (atomic), `usage.ensure_within_budgets`, `Moderator.check` (sends only), then the lease. Refusals (`RATE_LIMITED`, `BUDGET_EXCEEDED` with `details.scope`, `MODERATION_FLAGGED`) happen before anything is written. New run-starting endpoints must go through `RunService._admit`
- Spend lives in `usage_daily` (USD); anything that calls a model for a user adds to it (runs `requests=1`, regenerates and jobs `requests=0`). Budget policy is `core/budgets.py` (pure); reading spend is `services/usage.py`
- Moderation fails open and says so (log `moderation_skipped`, trace tag `moderation:skipped`); never log or echo the text

### Feedback and traces
- A run's Langfuse trace id is derived from the assistant message id (`Tracer.trace_id_for`) and stored in `messages.trace_id`; a rating (`services/feedback.py`) is stored first and then sent as the score `user_feedback`. Langfuse is a best-effort mirror: never fail a request on it, never send an unmasked comment
- Only the caller's own assistant messages can be rated (everything else is 404); a rating replaces the earlier one

### Logging
- structlog JSON only. Never log request bodies, query strings, headers or message content. Third-party stdlib loggers default to WARNING (client libraries log full URLs at INFO)

### Testing
- **Never mock the database.** Integration tests (`@pytest.mark.integration`) run against the Compose Postgres; each test gets its own database cloned from a migrated template (`tests/conftest.py`)
- Unit tests need nothing running: `make test`. Integration: `make up && make test-integration` (add `POSTGRES_PORT=...` if you changed the published port)
- Contract tests (`tests/contract/`) fuzz the running app against `docs/02_outputs/04_api_spec.yaml` with schemathesis; deterministic by default, `CONTRACT_FUZZ=random` explores new inputs. Narrow check exclusions must say why and when they go away
- Security properties get a test that fails when the property is broken; verify by mutating the code once

---

## Running Locally

```bash
cp .env.example .env                 # fill AUTH_SECRET/APP_SECRET (openssl rand -hex 32)
make up                              # whole stack in Docker; web http://localhost:8080
# or infrastructure only, API with hot reload:
docker compose up -d postgres qdrant
cd backend && uv sync
uv run python -m app.migrate
uv run uvicorn app.main:create_app --factory --reload --port 8000
uv run python -m app.cli invite create --note "me"
```

Host ports are configurable (`POSTGRES_PORT`, `QDRANT_PORT`, `APP_PORT`, `CADDY_HTTP_PORT`) for machines where 5432/6333/8000/8080 are taken; point `DATABASE_URL` at the new port for backend-only work.

## Running Tests

```bash
make lint                            # ruff, ruff format, mypy --strict, eslint, prettier, tsc
make test                            # backend unit + frontend unit (no services needed)
make test-integration                # needs `make up`; real Postgres, incl. contract tests
```

---

## Rules for AI Tools

- Read `memory/api.md` and `memory/auth.md` before modifying any handler
- The OpenAPI spec at `docs/02_outputs/04_api_spec.yaml` is the contract — do not add undocumented endpoints
- All new endpoints must have a corresponding entry in the API spec and a contract test in `tests/contract/`
- Run backend tests before marking any backend task as done

## AI Code Rules (if the backend has LLM features)

- Read `memory/ai.md` and `docs/02_outputs/08_ai_system_design.md` before touching prompts, graph, tools or retrieval
- Follow `AGENTS.md` §3.8: no inline prompts, everything traced, tenant-filtered retrieval, fake LLM in unit tests
- Model clients are created only through a single factory (timeouts, retries, fallback, tracing callbacks set there) — never instantiate a provider client ad hoc
- Long-running AI work (ingestion, embedding, memory extraction, titles) runs in background jobs, not on the request path
- After any AI behaviour change, run `make eval` and record results in the task file
