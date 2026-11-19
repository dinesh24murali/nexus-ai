# Memory: API

> Conventions and quirks of the Nexus AI HTTP API. Source of truth: `docs/02_outputs/04_api_spec.yaml`.

- Base path `/api/v1`; dev server `http://localhost:8000`
- Error envelope: `{"error": {"code": "UPPER_SNAKE", "message": "...", "details": {}, "request_id": "..."}}`
- Resources owned by another user → **404** (not 403) to prevent enumeration
- Cursor pagination: `?limit=1..100 (default 30)&cursor=<opaque>` → `{items, next_cursor}`; cursor encodes `created_at|id`
- `Idempotency-Key` header supported on `POST /files` and `POST /conversations/{id}/messages`
- Streaming endpoints (`POST …/messages`, `…/regenerate`, `…/runs/resume`) return SSE; payloads are the `StreamEvent` union; stream ends after `done`, `interrupt` or `error`
- One active run per conversation → second send returns 409 `RUN_IN_PROGRESS`
- Client disconnect does not cancel a run; only `POST …/runs/cancel` does
- 429 carries `Retry-After`; codes `RATE_LIMITED`, `BUDGET_EXCEEDED` (budget resets 00:00 UTC)
- Upload: multipart field `file`; 413 `FILE_TOO_LARGE` (> 20 MB), 415 `FILE_TYPE_UNSUPPORTED`, 409 `FILE_DUPLICATE`
- Spec lints clean with Redocly (warnings only: no license, placeholder server URLs, health probes without 4xx)
