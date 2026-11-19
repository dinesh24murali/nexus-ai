# backend/ — AGENTS.md

## Stack

- **Language:** `Python 3.12`
- **Framework:** `FastAPI`
- **Database:** `PostgreSQL 16`
- **Auth:** `JWT (access token + rotating refresh cookie)`

> Replace these placeholders with your actual stack after cloning.

---

## Project Structure

<!-- Document your backend folder structure here once it is established.
     Example for a Go/Gin project:
     backend/
     ├── cmd/server/        ← entry point
     ├── internal/
     │   ├── handlers/      ← HTTP handlers
     │   ├── services/      ← business logic
     │   ├── models/        ← data models
     │   └── db/            ← database access
     ├── migrations/        ← SQL migrations
     └── Makefile
-->

---

## Conventions

### API Handlers
<!-- Document conventions for your backend here. Example:
- Handlers only parse/validate input and delegate to services
- Services contain all business logic
- Never call the database directly from a handler
-->

### Error Handling
<!-- Document how errors are structured and returned. Example:
- All errors return JSON: {"error": {"code": "RESOURCE_NOT_FOUND", "message": "..."}}
- HTTP status codes follow RFC 7231
-->

### Database Access
<!-- Document ORM/query patterns, migration workflow, etc. -->

### Testing
<!-- Document how to run backend tests, what to mock vs. not mock, etc.
- Rule: do not mock the database in integration tests — use a real test DB
-->

---

## Running Locally

```bash
# Fill in with actual commands for Python 3.12
# Example for Go:
# cd backend && go run ./cmd/server
```

## Running Tests

```bash
# Fill in with actual test commands
# Example for Go:
# cd backend && go test ./...
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
