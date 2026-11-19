# ADR-0006: Server-Sent Events with Typed Events for Streaming

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

The UI must show tokens as they are generated, plus tool activity, citations and approval requests. The API spec is the contract and the frontend client is generated from it.

## Decision

Use **SSE over POST** (`text/event-stream`), consumed with `@microsoft/fetch-event-source`. Event payloads are a discriminated union (`StreamEvent`, keyed on `type`) declared in `docs/02_outputs/04_api_spec.yaml` components, so TypeScript types are generated. The stream client in `frontend/src/api/stream.ts` is the one allowed hand-written API client (AGENTS.md §3.7 exception). Client disconnect does not cancel a run; only `POST /runs/cancel` does.

## Consequences

### Positive
- Plain HTTP: works through Caddy and corporate proxies; auth via the normal Bearer header
- Event types are contract-tested like the rest of the API

### Negative
- No stream replay after disconnect in v1 (client polls and refetches)
- Proxies must not buffer (`flush_interval -1`, `X-Accel-Buffering: no`)

### Neutral
- One request per run; resume after approval opens a new stream

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| WebSockets | Bidirectional channel not needed; harder auth and proxying |
| Native `EventSource` | GET-only, cannot send a body or Authorization header |
| Polling | Poor UX and wasteful |

## References

- `docs/02_outputs/08_ai_system_design.md` §8
- `docs/02_outputs/04_api_spec.yaml` (`StreamEvent`)
