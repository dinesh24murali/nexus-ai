# ADR-0007: Postgres-Backed Job Queue

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

Ingestion, title generation, memory extraction and account purges must run off the request path with retries and idempotency. The chosen stack has no message broker, and expected volume is small (≈ hundreds of jobs/hour).

## Decision

Implement a `jobs` table in Postgres consumed by a worker process using `SELECT … FOR UPDATE SKIP LOCKED`, with `idempotency_key` uniqueness, `attempts/max_attempts`, exponential backoff via `run_after`, a 5-minute visibility timeout for stale `running` jobs, and graceful shutdown.

## Consequences

### Positive
- No extra infrastructure; jobs are enqueued in the same transaction as the data that triggers them
- Easy to inspect and replay with SQL

### Negative
- Polling latency (≈ 1 s) and lower throughput than a dedicated broker
- We own the queue code (small, but must be well-tested for concurrency)

### Neutral
- Can migrate to arq/Redis or a managed queue later behind the same `enqueue()` interface

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| Celery + Redis/RabbitMQ | Heavy for our volume; extra service |
| arq + Redis | Extra service just for jobs |
| FastAPI BackgroundTasks | Lost on restart; no retries or visibility |

## References

- `docs/02_outputs/08_ai_system_design.md` §9
