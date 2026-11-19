# Memory: Project

> Seed this file during project setup (task 000-project-setup).
> Update when the stack, team, or key decisions change.

---

## Project Identity

- **Name:** `Nexus AI`
- **Description:** `A production-grade, self-hostable ChatGPT-style assistant with streaming chat, document Q&A, tool use with human approval, and long-term memory.`
- **Team:** `Nexus AI Team`
- **Repo:** `https://github.com/dinesh24murali/nexus-ai`
- **Started:** `2026-11-18`

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Backend lang | Python | 3.12 |
| Backend framework | FastAPI (+ uvicorn, Pydantic 2, SQLAlchemy 2 async, Alembic) | 0.115 |
| AI orchestration | LangGraph (+ langchain-openai) | 0.4+ |
| Frontend | React + Vite + TypeScript, Tailwind + shadcn/ui, TanStack Query | 18.3 / 6 / 5.6 |
| Database | PostgreSQL (pgvector image) | 16 |
| Vector DB | Qdrant | 1.12 |
| LLM provider | OpenAI (gpt-4.1, gpt-4.1-mini, text-embedding-3-small) | — |
| LLM observability | Langfuse (self-hosted) | v3 |
| Auth | JWT access (15 min) + rotating refresh cookie (7 d) | |
| Deployment | Docker Compose on a single VM behind Caddy | |
| CI/CD | GitHub Actions | |

## Key Architectural Decisions

| Decision | ADR | Summary |
|----------|-----|---------|
| Use vibecode framework | ADR-0001 | File-based, AI-readable project structure |
| LangGraph orchestration | ADR-0002 | Graph + Postgres checkpointer/store; HITL via interrupt() |
| Qdrant + Postgres storage | ADR-0003 | Qdrant for chunks (tenant payload filter); Postgres for all else |
| Langfuse observability | ADR-0004 | Self-hosted; traces, prompt registry, datasets, scores |
| OpenAI model factory | ADR-0005 | All model clients created in `app/llm/factory.py` |
| SSE streaming | ADR-0006 | Typed `StreamEvent` union in the OpenAPI spec |
| Postgres job queue | ADR-0007 | SKIP LOCKED worker; no broker |

## Environments

| Env | URL | Notes |
|-----|-----|-------|
| Local | `http://localhost:8000` (API), `http://localhost:5173` (web), `http://localhost:3000` (Langfuse) | `make up` |
| Staging | `TBD` | |
| Production | `TBD` | |

## External Services

| Service | Purpose | Docs / Credentials |
|---------|---------|--------------------|
| OpenAI API | Chat, embeddings, moderation | platform.openai.com — `OPENAI_API_KEY` (project key with budget cap) |
| Tavily | Web search tool | app.tavily.com — `TAVILY_API_KEY` |
| Langfuse (self-hosted) | Tracing, prompts, evals | `LANGFUSE_*` keys from local Langfuse UI |

## Key Conventions (Quick Reference)

- API contract: `docs/02_outputs/04_api_spec.yaml` is the source of truth
- Frontend client is **generated** from the API spec — never hand-written
- All dates stored as UTC ISO 8601
- API prefix `/api/v1`; errors use the `{"error": {code, message, request_id}}` envelope
- Other users' resources return 404, never 403
- Cursor pagination everywhere (`limit`, `cursor` → `items`, `next_cursor`)
- AI changes follow AGENTS.md §3.8 and are gated by `make eval`

## Open Questions

- Staging/prod domains (TBD)
- Confirm OpenAI model names before launch
- Admin UI vs CLI-only; open vs invite-only registration
- Many intake answers are defaults chosen 2026-11-19 (marked `(default)` in the intake) — revisit
