# Scratch: 004-skeleton

> Written before any code, per AGENTS.md §3.1. Slices 1–6 and 8 implemented (see the task file progress log); the addendum at the end covers TASK-008.

---

## Plan

Slices in dependency order; each ends green (`make lint test`) and is one commit. Contract-first: the API spec already exists, so tests and the frontend client derive from it.

1. **Toolchain prerequisites (TASK-001 prep).** Install `uv`, then `uv python install 3.14`. Nothing else on the machine changes. (See Uncertainties U1.)
2. **Tooling (TASK-001).**
   - `backend/`: `uv init` on Python 3.14; deps `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `structlog`; dev deps `ruff`, `mypy` (`--strict`), `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx`. Commit `uv.lock`.
   - `frontend/`: Vite + React 19 + TS 5.9 template, `eslint`, `prettier`, `vitest` + Testing Library. Commit `package-lock.json`.
   - Root: `.pre-commit-config.yaml` (ruff, prettier, gitleaks), `Makefile` gains `lint`, `test`, `fmt` (existing targets untouched).
   - Fill the placeholder sections of `backend/AGENTS.md` and `frontend/AGENTS.md` (structure, run/test commands) as the structure is established.
3. **Config, logging, errors (TASK-004).** `core/config.py` (pydantic-settings; refuses `APP_ENV=production` + `LLM_MODE=fake`), `core/logging.py` (structlog JSON, request-id middleware, no bodies), `core/errors.py` (envelope matching the `Error` schema, handlers incl. 404/405/422/500), `main.py` with `/api/v1/healthz`, `/api/v1/readyz`. Tests first (TDD).
4. **Compose stack (TASK-002).** `compose.yml` (caddy, api, worker, migrate, postgres `pgvector/pgvector:pg18`, qdrant, healthchecks, named volumes, `stop_grace_period: 120s` on api), `compose.prod.yml`, multi-stage non-root Dockerfiles, `deploy/Caddyfile` (SSE `flush_interval -1`, CSP/HSTS headers, SPA static), `.env.example` additions only if needed. Optional `langfuse` profile is included but not run here.
   - The worker in M1 is an idle process that starts, logs and exits cleanly on SIGTERM (queue lands in TASK-012); `readyz` checks postgres + qdrant + langfuse reachability.
5. **DB layer & migrations (TASK-005).** SQLAlchemy 2 async models for all architecture §3.2 tables including `invites`, `runtime_flags` and the run-lease columns. The v1.1 pieces (`documents` table, `intent`/`route_source` columns) are deferred to M7 migrations; everything else in §3.2 lands now. Initial Alembic migration with the §3.3 indexes; `citext` + `pgcrypto`/uuid extension; migrate step also runs LangGraph `checkpointer.setup()` / `store.setup()` (needs `langgraph-checkpoint-postgres` — added now so the tables exist; no graph code yet). Repositories always take `user_id`. Integration tests against a real Postgres (service container locally via compose, no mocks).
6. **Auth, invites, admin CLI (TASK-006).** Endpoints from the spec: register (invite-gated), login, refresh (rotation + family revocation), logout, `/me` GET/PATCH, password change. argon2id, JWT HS256 15 min, refresh cookie `httpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`. Rate limits (Postgres token bucket, per-IP on auth + invite attempts). `app/cli.py`: `invite create|list|revoke`, `reset-password`, `disable-user|enable-user`, `flags set|list` with a 60 s cache in `core/runtime_flags.py`. Contract tests from the spec (schemathesis) in `tests/contract/`.
7. **Frontend shell (TASK-007).** `npm run gen:api` (openapi-typescript → `src/api/`), React Router SPA mode, auth context (in-memory access token + silent refresh), register page with `?invite=` prefill, tokens from design system doc 07 in `styles/tokens.css` + `theme.css` (`@theme`), light/dark/system theme, shadcn/ui init. Playwright `tests/e2e/auth.spec.ts`.
8. **Conversations CRUD (TASK-008).** `api/conversations.py` with cursor pagination (`base64(created_at|id)`), sidebar with infinite scroll and rename/delete menu; isolation test (`tests/backend/test_isolation.py`, starts with conversations); Playwright sidebar spec.
9. **CI (TASK-003).** `.github/workflows/ci.yml` (lint, mypy, tsc, unit + integration with Postgres/Qdrant service containers, contract tests, full-stack Playwright smoke with `LLM_MODE=fake`, image builds, pip-audit, npm audit, Trivy; GHCR push on `main`; prompt sync step is a no-op placeholder until TASK-009), `dependabot.yml`. Branch protection is a GitHub setting — I'll list the exact required checks, you enable them.
10. **Deploy + backups (TASK-033, TASK-034).** `deploy.yml` (SSH, pull, up, smoke `/readyz`), `deploy/README.md`, `memory/infra.md`, `deploy/backup.sh` (pg_dump + Qdrant snapshot), `deploy/uptime.md`; restore tested once locally and documented. **Needs your VM, secrets and bucket** — I'll prepare everything and stop at the point that needs them.
11. **Wrap-up.** Seed `tests/coverage-baseline.json` with measured per-package coverage (gates only ratchet up), update `memory/` (`api.md`, `auth.md`, `project.md`, new `infra.md`, `_index.md`), `CHANGELOG.md`, `make verify`, move task + scratch to `tasks/done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/pyproject.toml`, `backend/uv.lock` | create | Python 3.14, ruff/mypy/pytest config |
| `backend/app/main.py`, `backend/app/core/{config,logging,errors,security,ratelimit,runtime_flags}.py` | create | slices 3, 6 |
| `backend/app/api/{auth,me,conversations,health}.py` | create | contract from `04_api_spec.yaml` |
| `backend/app/db/{base,session,models}.py`, `backend/app/db/repositories/*` | create | all §3.2 tables except v1.1 `documents` |
| `backend/alembic/` + `alembic.ini` | create | initial migration, §3.3 indexes |
| `backend/app/{cli,worker}.py`, `backend/app/migrate.py` | create | CLI per plan; worker idle stub; migrate step |
| `backend/tests/**` | create | unit, integration (real Postgres), isolation |
| `backend/Dockerfile`, `frontend/Dockerfile` | create | multi-stage, non-root |
| `frontend/**` (Vite app, `src/api/` generated, `src/routes`, `src/lib/auth.ts`, `src/features/…`, `src/components/sidebar/`, `src/styles/{tokens,theme}.css`) | create | slices 2, 7, 8 |
| `tests/e2e/{auth,sidebar}.spec.ts`, `tests/contract/*`, `tests/backend/test_isolation.py` | create | |
| `compose.yml`, `compose.prod.yml`, `deploy/{Caddyfile,README.md,backup.sh,uptime.md}` | create | |
| `.github/workflows/{ci,deploy}.yml`, `.github/dependabot.yml` | create | |
| `.pre-commit-config.yaml` | create | |
| `Makefile` | modify | add `lint`, `test`, `fmt` |
| `.env.example` | modify (if needed) | only for genuinely new vars (e.g. `JWT_*`, rate-limit knobs) |
| `backend/AGENTS.md`, `frontend/AGENTS.md` | modify | fill structure/conventions/commands |
| `tests/coverage-baseline.json` | modify | per-package gates |
| `memory/{api,auth,project,infra,_index}.md`, `CHANGELOG.md` | modify/create | at completion |
| `docs/02_outputs/04_api_spec.yaml` | modify only if implementation reveals a gap | flag first, never silently diverge |

## AI Impact & Eval Plan

none. No prompts/models/tools/graph/retrieval/memory. `make eval` is not required for M1. Tracing: only the Langfuse client reachability check in `readyz`; no content is traced and logs carry no bodies (TASK-004 criterion).

## Uncertainties

- **U1 — Toolchain.** `uv` is not installed and the system Python is 3.12. Plan needs 3.14 via `uv python install 3.14`. Installing uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`, user-level, under `~/.local/bin`) changes your machine, so I'd like your OK first. Alternative: do everything in Docker.
- **U2 — Version claims in docs** (Vite 8, React Router 8, FastAPI 0.16x, Caddy 2.11, Qdrant 1.20, Postgres 18 pgvector image). I'll verify each against the registries at install time and pin exact versions in lockfiles; if something doesn't exist or differs materially I'll stop and flag it rather than silently substitute.
- **U3 — Postgres 18 volume path.** Plan says mount at `/var/lib/postgresql` (PG18 image changed its data dir layout). Will verify with a real container start.
- **U4 — LangGraph setup in M1.** Plan puts checkpointer/store `setup()` in the migrate step (TASK-005) although the graph arrives in M2. I'll include it now, with a test that the tables exist and `setup()` is idempotent. `store.setup()` with an embedding index requires pgvector; confirm the index config is deferrable to TASK-025 without a destructive migration later.
- **U5 — Langfuse in M1.** TASK-002 says "traces reach the Langfuse Cloud dev project", but tracing code is TASK-009. Proposal: M1 only proves connectivity (`readyz` + one smoke trace via the SDK) and the criterion is fully closed in TASK-009. OK?
- **U6 — Rate limiting storage.** Plan says Postgres token bucket; no table for it in §3.2. Proposal: an `rate_limits` table (`key`, `window_start`, `count`) added in the initial migration, or an UNLOGGED table. Needs your preference; I'll default to a plain table and note it in `memory/api.md`.
- **U7 — `.env` exists locally** (gitignored, contains real keys). I won't read, print or commit it; Compose will read it via `env_file`. Tests use `LLM_MODE=fake` and throwaway DB credentials from `.env.example`.
- **U8 — Prod prerequisites for slice 10:** VM address, domain for Caddy TLS, SSH deploy key as a GitHub secret, GHCR access, off-site bucket for backups, uptime-check provider. None needed until slice 10.

## What Will Be Skipped

- Everything outside M1: model factory, fake LLM, graph, SSE chat, job queue handlers, RAG, tools, memory, router, documents (M2+). The worker is an idle stub; `documents` table and `intent`/`route_source` columns wait for M7 migrations.
- Prometheus/metrics, data export, self-hosted Langfuse profile *run* (defined but not exercised).
- Real-model calls of any kind.
- Branch-protection rules themselves (GitHub setting, not repo code) — I'll supply the checklist.

## Risks

- **Scope.** Ten plan tasks in one group; mitigated by slicing and committing per slice. If slice 6 (auth) balloons, I'll split into its own task.
- **Auth is security-critical.** Refresh rotation/family revocation and invite uniform errors are subtle; mitigated by tests-first and the isolation/contract suites. The `INVITE_INVALID` uniformity needs timing-safe comparison on the hashed code.
- **Docs vs. reality drift** (U2/U3): a wrong pinned version would break `make up`; mitigated by verifying before pinning.
- **Deploy during an open browser session must not log the user out** (TASK-033): relies on refresh cookie + stable `AUTH_SECRET` across deploys; will be tested explicitly.
- **Blast radius:** greenfield repo, nothing running in prod yet; the main exposure is committing secrets — gitleaks pre-commit + CI scan guard that.

## Ready to implement?

**No — awaiting developer review.** Questions that change what I do next: U1 (install `uv`?), U5 (Langfuse criterion split), U6 (rate-limit table), and whether slices 1–6 should proceed before you've prepared the VM for slice 10.

---

## Addendum — TASK-008 only (Conversations CRUD), 2026-11-21

Scope is **TASK-008 and nothing else** (the developer asked for task 8 alone). State found: backend `api/conversations.py`, repository, schemas and 53 tests already exist; the frontend sidebar and `sidebar.spec.ts` do not.

**Plan**
1. Run the existing backend conversation + isolation tests against the Compose Postgres (`TEST_DATABASE_URL=…:5433`). **Found:** `repo.get` ignored `user_id` (Bob could read Alice's conversation; isolation test failed). Fix the filter. Fix 4 ruff errors in `test_conversations.py`.
2. Frontend data layer: `src/api/client.ts` (openapi-fetch typed from the generated `paths`, adapted onto `AuthManager.fetch` so refresh/retry still applies); `QueryClientProvider` in `main.tsx` and `test-utils.tsx`; hooks in `src/features/conversations/` (infinite list, get, create, rename, delete). Query keys include the user id so a cache can never leak across accounts on one tab; cache cleared on sign-out.
3. `src/components/sidebar/`: `Sidebar`, `ConversationList` (infinite scroll via IntersectionObserver + "Load more" fallback), `ConversationItem` (link, actions menu, inline rename, inline delete confirm). Route `/c/:conversationId` (`ConversationPage`: title, 404 state) so "get" has a consumer; deleting the open conversation goes home. New chat creates a conversation and opens it.
4. Tests: Vitest component tests (fetch scripted); Playwright `tests/e2e/sidebar.spec.ts` (create, rename, delete, reload persistence, infinite scroll over >30 rows seeded via the API, cross-user URL shows not-found).
5. Wrap-up for this task only: tick TASK-008 criteria, progress log, `memory/api.md` note, `frontend/AGENTS.md` structure line. **Not** moving the task file to `done/` or touching `CHANGELOG.md` — the parent task `004-skeleton` is still active (TASK-003, 033, 034 open).

**Uncertainties:** openapi-fetch hands `fetch` a `Request`; `AuthManager.fetch` takes `(url, init)` — adapter in `client.ts` rather than changing auth code (slice 7 is out of scope).
**Skipped:** chat/messages/composer (M2), run cancel on delete (TODO already in API, TASK-011), search, drag/reorder, TanStack Query persistence.
**Files:** `backend/app/db/repositories/conversations.py`, `backend/tests/api/test_conversations.py`, `frontend/src/{main.tsx,App.tsx,test-utils.tsx,api/client.ts,features/conversations/*,components/sidebar/*,routes/{AppShell,ConversationPage}.tsx}`, `tests/e2e/{sidebar.spec.ts,support/api.ts}`, `memory/api.md`, `frontend/AGENTS.md`, task file.
