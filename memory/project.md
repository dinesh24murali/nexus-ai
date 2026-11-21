# Memory: Project

> Seed this file during project setup (task 000-project-setup).
> Update when the stack, team, or key decisions change.

---

## Project Identity

- **Name:** `Nexus AI`
- **Description:** `A production-grade, self-hostable ChatGPT-style assistant with streaming chat, document Q&A, tool use with human approval, and long-term memory.`
- **What it is:** a **reference build** the developer runs themselves; the hosted instance is **invite-only**. "Production-grade" = engineering gates (evals, tracing, tests, isolation, cost caps), not an operated service with an SLO
- **Team:** one developer working part-time with AI coding agents; milestones set order, not dates
- **Repo:** `https://github.com/dinesh24murali/nexus-ai`
- **Started:** `2026-11-18`

## Tech Stack

Docs name major versions; exact pins live in the lockfiles (versions checked 2026-11-19).

| Layer | Technology | Version |
|-------|-----------|---------|
| Backend lang | Python | 3.14 |
| Backend framework | FastAPI (+ uvicorn, Pydantic 2, SQLAlchemy 2 async, Alembic) | 0.16x |
| DB driver | psycopg 3 (only Postgres driver, also used by LangGraph) | 3.x |
| AI orchestration | LangGraph (+ langchain-openai, Responses API) | 1.x |
| Frontend | React + Vite + TypeScript, Tailwind 4 + shadcn/ui, TanStack Query, React Router (SPA mode) | 19 / 8 / 5.9 / 4 / 5 / 8 |
| Database | PostgreSQL (`pgvector/pgvector:pg18` image) | 18 |
| Vector DB | Qdrant | 1.x |
| LLM provider | OpenAI (models in `memory/ai.md`) | — |
| LLM observability | Langfuse Cloud, EU region, Hobby plan (SDK v5); self-hosted v4 optional | — |
| Auth | JWT access (15 min) + rotating refresh cookie (7 d); invite codes | |
| Deployment | Docker Compose on a single VM behind Caddy 2.11 | |
| CI/CD | GitHub Actions (CI = staging; manual prod deploy) | |

## Key Architectural Decisions

| Decision | ADR | Summary |
|----------|-----|---------|
| Use vibecode framework | ADR-0001 | File-based, AI-readable project structure |
| LangGraph orchestration | ADR-0002 | Graph + Postgres checkpointer/store; HITL via interrupt(); `TOOL_POLICY` |
| Qdrant + Postgres storage | ADR-0003 | Qdrant for chunks (tenant payload filter); Postgres for all else; cross-store purge test |
| Langfuse observability | ADR-0004 | Langfuse Cloud EU by default; self-hosted v4 optional; redaction mandatory |
| OpenAI model factory | ADR-0005 | One factory; reasoning effort per role; Responses API `store=false`; `LLM_MODE=fake` |
| SSE streaming + run lease | ADR-0006 | Typed `StreamEvent` union; hand-written client; lease + reaper + graceful drain |
| Postgres job queue | ADR-0007 | SKIP LOCKED worker + periodic tasks; no broker |
| Repo-canonical prompts | ADR-0008 | `backend/prompts/` is the source; CI mirrors to Langfuse |
| Two-tier quality gates | ADR-0009 | Targets vs. measured gates that ratchet up; absolute safety gates; full suite weekly |
| Intent router (v1.1) | ADR-0010 | Router over 4 intents + deterministic slash commands; tuned confidence threshold; schedules in the backlog |

## Environments

| Env | URL | Notes |
|-----|-----|-------|
| Local | `http://localhost:8000` (API), `http://localhost:5173` (web) | `make up`; Langfuse Cloud dev project; `LLM_MODE=fake` offline |
| CI ("staging") | GitHub Actions | Full stack with fake LLM + Playwright; eval smoke with real models |
| Production | `TBD` (one VM, invite-only) | Manual `deploy.yml`; nightly backups |

## External Services

| Service | Purpose | Docs / Credentials |
|---------|---------|--------------------|
| OpenAI API | Chat, embeddings, moderation (Responses API, `store=false`) | platform.openai.com — `OPENAI_API_KEY` (project key with budget cap; separate CI key) |
| Tavily | Web search tool | app.tavily.com — `TAVILY_API_KEY` |
| Langfuse Cloud (EU) | Tracing, datasets, scores, prompt mirror | cloud.langfuse.com — `LANGFUSE_*` keys per environment project |

## Key Conventions (Quick Reference)

- API contract: `docs/02_outputs/04_api_spec.yaml` is the source of truth
- Frontend client is **generated** from the API spec — never hand-written (except `src/api/stream.ts`)
- All dates stored as UTC ISO 8601
- API prefix `/api/v1`; errors use the `{"error": {code, message, request_id}}` envelope
- Other users' resources return 404, never 403
- Cursor pagination everywhere (`limit`, `cursor` → `items`, `next_cursor`)
- AI changes follow AGENTS.md §3.8 and are gated by `make eval` (two-tier gate, ADR-0009)
- Admin actions are CLI-only: `python -m app.cli invite|reset-password|disable-user|enable-user`

## Open Questions

- Production domain (TBD)
- Chat model decided by the M2 bake-off (TASK-036): `gpt-6.1-terra` vs `gpt-5.4-mini-2026-03-17`
- Plan revised 2026-11-19 after a grilling session (task 003); remaining intake defaults accepted
- v1.1 scope (M7) revised 2026-11-21 after a second grilling session (task 003b): router + memory by chat + PDFs + deep research; schedules and the small-talk fast path cut; `remember_fact` removed from v1; kill switches in a `runtime_flags` table
