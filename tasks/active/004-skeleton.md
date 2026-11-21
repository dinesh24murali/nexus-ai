# Task: 004-skeleton

**Status:** active
**Created:** 2026-11-21
**ADR refs:** ADR-0001, ADR-0006 (event schema only, not implemented here), ADR-0007 (queue is M2), ADR-0009 (coverage gates)

---

## Goal

Ship the M1 skeleton: monorepo tooling, Compose stack, CI, config/logging/errors, DB layer, auth with invites and admin CLI, frontend shell, conversations CRUD, deploy workflow and backups — **live on the VM**.

## Background

Implementation plan v1.3 (`docs/02_outputs/05_implementation_plan.md`), milestone M1, roadmap group `004-skeleton`. Covers TASK-001 … TASK-008, TASK-033, TASK-034. Every milestone ends deployed: run `deploy.yml`, check `/readyz`, chat smoke (no chat in M1, so smoke = register → login → create/rename/delete a conversation).

## Acceptance Criteria

Copied from the plan; check off as met.

**TASK-001 Monorepo tooling**
- [ ] `make lint` and `make test` pass on an empty project
- [ ] pre-commit runs ruff, prettier, and gitleaks

**TASK-002 Docker Compose stack**
- [ ] `make up` brings every default service to healthy in < 2 min on a clean machine
- [ ] API at `http://localhost:8000/api/v1/healthz`; traces reach the Langfuse Cloud dev project *(trace part: only the Langfuse client wiring and `/readyz` check exist in M1; real traces arrive with TASK-009 — see scratch)*

**TASK-003 CI pipeline**
- [ ] Pipeline is green on the first PR; required checks enforced on `main`

**TASK-004 Config, logging, errors**
- [ ] Unknown routes return the error envelope with `request_id`
- [ ] Logs carry no request bodies

**TASK-005 Database layer & migrations**
- [ ] `alembic upgrade head` on an empty DB creates all tables and indexes
- [ ] The migrate service is idempotent

**TASK-006 Auth, invites & admin CLI**
- [ ] Refresh-token reuse revokes the whole family (tested)
- [ ] Login does not reveal whether an email exists
- [ ] Unknown, used, revoked and expired invite codes all return the same `INVITE_INVALID` (tested)
- [ ] Contract tests for the auth endpoints pass
- [ ] A flag set with the CLI takes effect in a running API within 60 s without a restart (tested)

**TASK-007 Frontend shell**
- [ ] Invite → register → login work end-to-end against the API
- [ ] Playwright `auth.spec.ts` passes

**TASK-008 Conversations CRUD**
- [x] Cross-user access returns 404 (isolation test)
- [x] Sidebar e2e test passes

**TASK-033 Deploy workflow**
- [ ] The M1 skeleton is live on the VM; a deploy during an open browser session does not log the user out

**TASK-034 Backups & uptime check**
- [ ] A restore into a fresh local stack succeeds once and is documented

## Dependencies

- **Tasks:** none (first build task; 003/003b were doc revisions)
- **Memory files:** `memory/project.md`, `memory/api.md`, `memory/auth.md` (`memory/ai.md` is not touched in M1)
- **Docs:** PRD §12 (M1), architecture §2–§6, API spec `04_api_spec.yaml` (auth, me, conversations, healthz/readyz), design system `07_design_system.md` (tokens), dev setup `06_dev_setup.md`

## AI Impact

none — no prompts, models, tools, graph, retrieval or memory in M1. (The model factory, tracing and fake LLM start in TASK-009.)

## Files Expected to Change

See `004-skeleton.scratch.md`.

## Progress log

- 2026-11-21 slice 1–2 (TASK-001) done: uv + Python 3.14.10 installed by the developer; backend (ruff, mypy strict, pytest) and frontend (Vite 8, React 19, TS 5.9 pinned — the template defaulted to TS 7 + oxlint, replaced with eslint per plan); `make lint|test|fmt|hooks`; pre-commit (ruff, prettier, gitleaks) verified with `pre-commit run --all-files`.
- 2026-11-21 slice 3 (TASK-004) done: config (prod refuses `LLM_MODE=fake` and weak secrets), JSON logging + pure-ASGI request-id middleware, error envelope (422 `VALIDATION_FAILED` never echoes input), `/healthz` + `/readyz`. Found and fixed: HTTP client libraries log full URLs (query strings) at INFO → every third-party stdlib logger now defaults to WARNING (first attempt capped only `httpx`; adding LangGraph pulled in `httpx2` and re-opened the leak).
- 2026-11-21 slice 4 (TASK-002) done: compose stack healthy (postgres pg18/pgvector, qdrant v1.20.0, api, worker, caddy non-root). Warm `up --wait` 16 s; cold build ~9 min (image pulls + npm/uv installs). PG18 data persists with volume at `/var/lib/postgresql` (U3 confirmed; data dir `/var/lib/postgresql/18/docker`). Host port 5432 is occupied on the dev machine by a local Postgres, so host ports are configurable (`POSTGRES_PORT`, `QDRANT_PORT`, `APP_PORT`). npm lock regenerated with the container's npm (the local npm 11.6 omitted optional `@emnapi/*` entries and `npm ci` failed in Docker/CI).
- 2026-11-21 slice 5 (TASK-005) done: all v1 §3.2 tables + `rate_limits` (U6: plain table), Alembic async env, initial migration hand-reviewed (added the missing `CREATE EXTENSION citext`), `python -m app.migrate` (Alembic + LangGraph `checkpointer.setup()`/`store.setup()`; idempotent; compose `migrate` service gates api/worker). Integration tests run on a real Postgres via per-test databases cloned from a migrated template; a drift test fails if models and migrations disagree. U4: LangGraph setup works without the embedding index; the index (pgvector) is added with TASK-025 — verify it applies non-destructively then.
- 2026-11-21 slice 6 (TASK-006) done: argon2id, JWT with `fid`, invite-gated register, login, rotating refresh with family revocation, logout, `/me` GET/PATCH, password change, Postgres rate limits, runtime flags (60 s cache, stale-on-DB-failure), admin CLI. 170 backend tests (69 unit, 101 integration), ~95% coverage. Mutation-checked the security properties (reuse revocation, invite row lock, enumeration order, session preservation on password change). Schemathesis contract tests found 5 genuine spec/implementation mismatches, all fixed in code, not by loosening checks: login password max-length, null/strict-bool on PATCH /me, undecodable-body 400, reserved-domain emails, empty PATCH. Live-smoked through Caddy with the real CLI.
- 2026-11-21 slice 8 (TASK-008) done: conversations list/create/get/rename/delete with keyset cursor pagination; sidebar with infinite scroll (IntersectionObserver + "Load more" fallback), actions menu, inline rename, inline delete confirm; `/c/:id` page (title, not-found state); typed `openapi-fetch` client over `AuthManager.fetch`; TanStack Query with the user id in every key and the cache cleared on sign-out. 234 backend tests (96% coverage), 77 frontend tests (93% statements), 11 Playwright tests against the rebuilt Compose stack, `check-dom.ts` green. **Found and fixed:** (1) `repo.get` never filtered on `user_id`, so any user could read another's conversation — caught by the isolation test; (2) a NUL character in a title reached Postgres and returned a 500 on create/rename — now 422; (3) `POST /conversations` accepted a JSON `null` body (spec: not nullable) — now 422, missing body still allowed; (4) `?cursor=` (empty) returned 400 though schema-valid — now the first page. Contract tests: positive-data-acceptance is excluded for `GET`/`POST`/`PATCH` conversations (opaque cursor; title rules stricter than `minLength: 1`), same pattern as `POST /me/password`. **Decision for the developer:** tighten the spec instead (title `pattern`, cursor format) and drop those exclusions? Tests need `TEST_DATABASE_URL` (the dev machine's Compose Postgres is on 5433).
- Contract tests live in `backend/tests/contract/` (they need the app's fixtures), not repo-root `tests/contract/` as AGENTS.md says — **needs the developer's decision** (see end of task).

- 2026-11-21 (found during 005 TASK-011): schemathesis, once the contract scope grew, found that a NUL character in `display_name` or an email returned 500 (Postgres text cannot hold NUL). Fixed in code (422, tested for PATCH /me, register and login). The contract test now excludes positive-data acceptance for those three operations, same pattern as titles. **Decision for the developer (extends the title question above):** add a `pattern` excluding NUL to `display_name`, `email`, `title` and message `content` in the spec and drop all these exclusions?

## Notes

- Execution is sliced (scratch §Plan) so each slice is a reviewable commit/PR; if it grows, split slices 7–8 (CI, deploy) into their own task.
- TASK-033/034 need things only the developer can provide (VM, domain, SSH key, GitHub secrets, off-site bucket). Slices 1–6 are fully local.
- Scratch file is the contract: no code until the developer has reviewed it.
