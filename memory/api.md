# Memory: API

> Conventions and quirks of the Nexus AI HTTP API. Source of truth: `docs/02_outputs/04_api_spec.yaml`.

- Base path `/api/v1`; dev server `http://localhost:8000`
- Error envelope: `{"error": {"code": "UPPER_SNAKE", "message": "...", "details": {}, "request_id": "..."}}`
- Resources owned by another user → **404** (not 403) to prevent enumeration
- Cursor pagination: `?limit=1..100 (default 30)&cursor=<opaque>` → `{items, next_cursor}`; cursor encodes `created_at|id`
- `Idempotency-Key` header supported on `POST /files` and `POST /conversations/{id}/messages`
- Streaming endpoints (`POST …/messages`, `…/regenerate`, `…/runs/resume`) return SSE; payloads are the `StreamEvent` union; stream ends after `done`, `interrupt` or `error`
- One active run per conversation (run lease) → second send returns 409 `RUN_IN_PROGRESS`; a lease left by a crashed API is reaped within ~60 s and the assistant message gets `status=failed`, `finish_reason=server_restart`
- Client disconnect does not cancel a run; only `POST …/runs/cancel` does
- During a deploy, run-starting endpoints return 503 `SERVICE_RESTARTING` with `Retry-After`
- 429 carries `Retry-After`; codes `RATE_LIMITED`, `BUDGET_EXCEEDED` with `details.scope = user | global` (USD budgets reset 00:00 UTC)
- `POST /auth/register` takes `invite_code` (required when `REGISTRATION_MODE=invite`); bad codes → 403 `INVITE_INVALID` (one response for unknown/used/revoked/expired)
- No data export endpoint in v1 (FR-005 deferred)
- Upload: multipart field `file`; 413 `FILE_TOO_LARGE` (> 20 MB), 415 `FILE_TYPE_UNSUPPORTED`, 409 `FILE_DUPLICATE`
- Spec lints clean with Redocly (warnings only: no license, placeholder server URL, health probes without 4xx); spec version 1.1.0
- **Implementation facts (TASK-004/006, 2026-11-21):**
  - Every response carries `X-Request-ID` (`req_<16 hex>`); a client-supplied one is honoured only if it matches that shape. Error bodies carry the same id
  - Any malformed or invalid body is `422 VALIDATION_FAILED` with `details.fields[] = {loc, msg, type}`; submitted values are never echoed. (FastAPI's generic 400 for undecodable bodies is mapped to 422)
  - `PATCH /me`: fields are optional but **not nullable**; `{}` is a valid no-op that returns the user; `memory_enabled` is a strict boolean
  - `/readyz`: Postgres and Qdrant are required (503 if down); Langfuse is optional and only checked when keys are configured (`degraded`, still 200). `/healthz` is liveness only
  - 404/405 and unknown routes use the error envelope (`NOT_FOUND`, `METHOD_NOT_ALLOWED`)
  - Contract tests: `backend/tests/contract/` (schemathesis against the spec, deterministic; `CONTRACT_FUZZ=random` to explore). Exclusions: Allow-header check until `DELETE /me` exists (TASK-028); positive-data acceptance on `POST /me/password`
- **Conversations (TASK-008, 2026-11-21):**
  - List order is `updated_at DESC, id ASC` (keyset; cursor = base64url `updated_at|id`, matches index `ix_conversations_user_updated`). Rename deliberately does NOT bump `updated_at` (it means last activity). `?cursor=` empty = first page; a cursor the server did not issue → 400 `BAD_REQUEST`
  - Another user's id, an unknown id and a malformed id are all the same 404 (never 422/403). Every repository function takes `user_id`; `repositories/conversations.get` once forgot to filter on it — keep `tests/api/test_isolation.py` growing with each user-owned resource
  - Title: trimmed, 1–100 chars, no NUL (Postgres text cannot store it → 422 instead of a 500). `POST /conversations` body is optional but a JSON `null` body is 422
  - Delete removes messages (FK cascade) and LangGraph `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` rows for the thread; cancelling an active run first is TODO(TASK-011)
  - Backend integration tests read `TEST_DATABASE_URL` (default port 5432; Compose Postgres on a dev machine with a local Postgres is on 5433)
