# ADR-0006: Server-Sent Events with Typed Events for Streaming

**Status:** Accepted
**Date:** 2026-11-19
**Revised:** 2026-11-19 (task 003: hand-written stream client, run lease + reaper + graceful drain)
**Deciders:** Nexus AI Team

---

## Context

The UI must show tokens as they are generated, plus tool activity, citations and approval requests. The API spec is the contract and the frontend client is generated from it. Runs execute as asyncio tasks inside the API process and are not cancelled by client disconnects, so an API restart (deploy, OOM kill) can leave a run half-finished.

## Decision

**Transport.** Use **SSE over POST** (`text/event-stream`). Event payloads are a discriminated union (`StreamEvent`, keyed on `type`) declared in `docs/02_outputs/04_api_spec.yaml` components, so TypeScript types are generated. The stream client `frontend/src/api/stream.ts` is hand-written (≈ 80 lines: `fetch` + `ReadableStream` + a line-based SSE parser + `AbortController`), the one allowed hand-written API client (AGENTS.md §3.7 exception), and is unit-tested against recorded stream fixtures. Client disconnect does not cancel a run; only `POST /runs/cancel` does.

**Run lease.** While a run executes, `conversations.active_run_id` is set and the API updates `conversations.run_heartbeat_at` every 10 s. When a run reaches `done`, `error` or `interrupt`, the lease is cleared (a run awaiting approval holds no lease; its state is in `pending_interrupt`).

**Reaper.** The worker runs `reap_stale_runs` every 30 s: any lease whose heartbeat is older than 60 s is cleared, and a `failed` assistant message (partial text if any, `finish_reason=server_restart`) is written, so the conversation never stays locked.

**Graceful drain.** On SIGTERM the API stops accepting new runs (503 `SERVICE_RESTARTING`, retryable) and waits up to 90 s for in-flight runs (Compose `stop_grace_period: 120s`). Runs still going at the deadline are saved as `failed` with their partial text.

## Consequences

### Positive
- Plain HTTP: works through Caddy and corporate proxies; auth via the normal Bearer header
- Event types are contract-tested like the rest of the API
- No unmaintained streaming dependency (`@microsoft/fetch-event-source` has had no commits since 2023)
- Deploys and crashes never leave a conversation locked

### Negative
- No stream replay after disconnect in v1 (client polls and refetches)
- Proxies must not buffer (`flush_interval -1`, `X-Accel-Buffering: no`)
- We own a small SSE parser

### Neutral
- One request per run; resume after approval opens a new stream

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| WebSockets | Bidirectional channel not needed; harder auth and proxying |
| Native `EventSource` | GET-only, cannot send a body or Authorization header |
| `@microsoft/fetch-event-source` | Unmaintained since 2023 |
| Run graphs in the worker, stream via Postgres LISTEN/NOTIFY | More moving parts; the lease + reaper covers restarts at a fraction of the cost |
| Polling | Poor UX and wasteful |

## References

- `docs/02_outputs/08_ai_system_design.md` §2.3, §8
- `docs/02_outputs/04_api_spec.yaml` (`StreamEvent`)
- ADR-0007 (worker hosts the reaper)
