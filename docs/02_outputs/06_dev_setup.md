# Developer Setup Guide — Nexus AI

**Version:** 1.0 · **Date:** 2026-11-19

> **Note:** This guide describes the target setup. Commands that depend on code from implementation-plan TASK-001/002 (Compose stack, uv project, Vite app) work once those tasks land.

## 1. Prerequisites

| Tool | Min version | Install |
|------|-------------|---------|
| Git | 2.40 | https://git-scm.com/downloads |
| Docker Engine + Compose v2 | 25 / 2.24 | https://docs.docker.com/engine/install/ (macOS/Windows: Docker Desktop) |
| Python | 3.12 | https://www.python.org/downloads/ (or `uv python install 3.12`) |
| uv | 0.5 | https://docs.astral.sh/uv/getting-started/installation/ |
| Node.js | 22 LTS | https://nodejs.org/ (or nvm) |
| GNU Make | 4.x | Linux: preinstalled; macOS: `xcode-select --install`; Windows: use WSL2 |
| Playwright browsers | latest | `npx playwright install --with-deps chromium` |

> **Warning:** On Windows, develop inside WSL2 (Ubuntu). Bind mounts from the Windows filesystem make hot reload and Postgres slow.

Allocate ≥ 6 GB RAM to Docker — Langfuse's ClickHouse is the heaviest service.

## 2. Repository Setup

```bash
git clone git@github.com:dinesh24murali/nexus-ai.git
cd nexus-ai
cp .env.example .env
```

Fill these values in `.env`:

| Variable | Where to get it |
|----------|-----------------|
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys — create a **project** key with a monthly budget |
| `TAVILY_API_KEY` | https://app.tavily.com (free tier is enough for dev) |
| `AUTH_SECRET`, `APP_SECRET` | `openssl rand -hex 32` (each) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | After first `make up`: open http://localhost:3000, create org + project → Settings → API keys |
| `DATABASE_URL` | Defaults in `.env.example` match Compose |

```bash
make check-env   # verifies every variable in .env.example is set
```

> **Warning:** Never commit `.env`. The pre-commit hook runs gitleaks, but don't rely on it.

## 3. Local Development

**Full stack in Docker:**

```bash
make up                  # docker compose up -d --build
docker compose ps        # all services should be "healthy"
curl -s http://localhost:8000/api/v1/readyz
# {"status":"ok","checks":{"postgres":"ok","qdrant":"ok","langfuse":"ok"}}
```

| URL | Service |
|-----|---------|
| http://localhost:5173 | Web (Vite dev server) |
| http://localhost:8000/docs | API (Swagger UI) |
| http://localhost:3000 | Langfuse |
| http://localhost:6333/dashboard | Qdrant dashboard |

**Backend only (hot reload), infrastructure in Docker:**

```bash
docker compose up -d postgres qdrant langfuse-web langfuse-worker clickhouse redis minio
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
# second terminal
uv run python -m app.worker
```

**Frontend only:**

```bash
cd frontend
npm ci
npm run gen:api      # regenerate client from docs/02_outputs/04_api_spec.yaml
npm run dev          # http://localhost:5173, proxies /api to :8000
```

**No OpenAI key / offline:** set `LLM_FAKE=1` in `.env` to use a scripted fake model (deterministic replies, fake tool calls). Useful for UI work and e2e tests.

## 4. Database Setup

```bash
cd backend
uv run alembic upgrade head                    # migrate (also creates LangGraph tables)
uv run python -m app.cli seed                  # demo user demo@nexus.local / demo-password-123 + sample files
uv run alembic revision --autogenerate -m "add x"   # new migration (review the generated file!)
```

Reset everything (deletes all local data):

```bash
docker compose down -v && make up
```

## 5. Running Tests

```bash
make test                                   # backend + frontend unit tests
cd backend && uv run pytest -m "not integration"     # backend unit only (fake LLM)
cd backend && uv run pytest -m integration           # needs postgres + qdrant running
cd backend && uv run pytest --cov=app --cov-report=term-missing
cd frontend && npm test -- --coverage
make verify                                 # Playwright e2e + DOM checks (stack must be up, LLM_FAKE=1 recommended)
make eval-smoke                             # small eval set with real models (~$0.20)
make eval                                   # full eval set (~$2)
```

Coverage thresholds live in `tests/coverage-baseline.json`; eval thresholds in `evals/eval-baseline.json`.

## 6. Common Development Tasks

**Add an API endpoint**
1. Add it to `docs/02_outputs/04_api_spec.yaml` first (contract-first).
2. `cd frontend && npm run gen:api`.
3. Implement the router in `backend/app/api/`, logic in `backend/app/services/`, data access in `backend/app/db/repositories/` (always filter by `user_id`).
4. Add a contract test in `tests/contract/` and an isolation test if the resource is user-owned.

**Add a frontend page**
1. Add a route in `frontend/src/routes/`, page under `frontend/src/features/<area>/`.
2. Use generated types from `src/api/`; tokens from `src/styles/tokens.css`.
3. Add `tests/e2e/<feature>.spec.ts`; run `make verify`.

**Add or change a prompt / tool / model** — follow `AGENTS.md` §3.8: edit the prompt in Langfuse *and* the fallback in `backend/prompts/`, run `make eval`, update `memory/ai.md`.

**New migration:** `cd backend && uv run alembic revision --autogenerate -m "<what>"`.
**New ADR:** `make adr SLUG=<slug>`. **New task:** `make task NAME=<NNN-slug>`.

## 7. Debugging

- **Debug logs:** `LOG_LEVEL=DEBUG` in `.env`; `docker compose logs -f api worker`.
- **LLM behaviour:** open the trace in Langfuse — the `trace_id` is in the API log line and in `messages.trace_id`.
- **Backend debugger:** VS Code → "Python: Remote Attach" on port 5678 after starting with `uv run python -m debugpy --listen 5678 -m uvicorn app.main:app --reload`.
- **Frontend:** browser devtools; React DevTools; the Network tab shows SSE frames under the request's EventStream tab.
- **LangGraph state:** `uv run python -m app.cli dump-thread <conversation_id>` prints the latest checkpoint.

| Symptom | Fix |
|---------|-----|
| SSE arrives all at once | Proxy is buffering — check Caddy `flush_interval -1`; in dev use Vite proxy with `ws: false` |
| `readyz` shows `langfuse: unavailable` | Langfuse takes ~60 s on first boot (ClickHouse migrations); tracing degrades gracefully |
| `401 token_expired` loops | Clock skew in Docker VM; restart Docker Desktop |
| File stuck in `processing` | Worker not running: `docker compose logs worker` |
| `429 insufficient_quota` from OpenAI | Project key budget exhausted |

## 8. CI/CD Overview

- **On PR:** lint (ruff, eslint), type-check (mypy, tsc), unit + integration tests (Postgres/Qdrant service containers), contract tests (schemathesis), coverage gate, security scans, Docker build. `eval-smoke` runs when the PR has the `ai-change` label or touches `backend/app/{graph,llm,rag,memory}/` or `backend/prompts/`.
- **On merge to `main`:** all of the above + `eval-smoke`; push images to GHCR; deploy to staging; Playwright smoke against staging.
- **Release:** manual `workflow_dispatch` promotes the staging image tag to prod after a full `make eval`.
- **Status:** GitHub → Actions tab, or `gh run list --branch main`.

## 9. Deployment (non-prod)

```bash
gh workflow run deploy.yml -f environment=staging -f ref=main
# or manually on the staging VM:
ssh deploy@staging 'cd /opt/nexus-ai && docker compose -f compose.yml -f compose.prod.yml pull && docker compose -f compose.yml -f compose.prod.yml up -d'
```

> **Warning:** Staging uses its own OpenAI project key and Langfuse project. Never point staging at production data.
