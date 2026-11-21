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
│   │                         feedback.py (PUT /messages/{id}/feedback), files.py (upload, list, get)
│   ├── services/           ← use cases and transaction boundaries (auth.py; files.py = upload checks, storage,
│   │                         duplicate/limit, enqueue `ingest_file`; runs.py = run lease,
│   │                         streaming, cancel, regenerate, drain, pre-run limits; usage.py = spend reads,
│   │                         budget check, /me/usage, spend alert; feedback.py = rate an answer + Langfuse score)
│   ├── jobs/               ← queue.py (Postgres SKIP LOCKED), handlers.py (registry: kind -> handler,
│   │                         timeout, attempts), periodic.py (reaper, stale-job requeue, cleanups)
│   ├── db/                 ← base, models, session, repositories/ (every query is owner-scoped)
│   ├── core/               ← config, logging, errors, security, ratelimit, budgets (policy), runtime_flags, health, net
│   ├── cli.py              ← admin CLI (python -m app.cli); worker.py (job loops + periodic); migrate.py
│   ├── llm/                ← factory.py (the only place provider clients are built), fake.py (LLM_MODE=fake),
│   │                         prompts.py + prompt_sync.py, tracing.py, redaction.py, pricing.py, tokens.py,
│   │                         moderation.py (input moderation, fail-open),
│   │                         usage.py (per-run token + cost collector)
│   ├── graph/              ← state.py, prompting.py (cache-friendly request layout), builder.py, nodes/
│   ├── rag/                ← formats.py, parsers.py (PDF/DOCX/TXT/MD), chunker.py (800/120 tokens), embedder.py,
│   │                         index.py (Qdrant: alias `documents`, tenant payload); ingestion job is in jobs/handlers.py
│   └── (memory/ ...        arrives with M5; layout in docs/02_outputs/03_tech_architecture.md §2)
├── evals/                  ← the eval suite (`python -m evals.run`; see evals/AGENTS.md); not imported by app/
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
