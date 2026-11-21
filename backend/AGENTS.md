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
│   ├── api/                ← routers (thin), deps.py (auth + session factory), schemas.py
│   ├── services/           ← use cases and transaction boundaries (services/auth.py)
│   ├── db/                 ← base, models, session, repositories/ (every query is owner-scoped)
│   ├── core/               ← config, logging, errors, security, ratelimit, runtime_flags, health, net
│   ├── cli.py              ← admin CLI (python -m app.cli); worker.py; migrate.py
│   └── (graph/ llm/ rag/ memory/ jobs/ ...  arrive with M2+; layout in docs/02_outputs/03_tech_architecture.md §2)
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
