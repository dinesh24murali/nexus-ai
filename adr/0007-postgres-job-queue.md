# ADR-0007: Postgres-Backed Job Queue

**Status:** Accepted
**Date:** 2026-11-19
**Revised:** 2026-11-19 (task 003: periodic `reap_stale_runs` job, psycopg 3); 2026-11-21 (task 003b: job heartbeat and per-kind timeouts replace the fixed 5-minute visibility timeout)
**Deciders:** Nexus AI Team

---

## Context

Ingestion, title generation, memory extraction and account purges must run off the request path with retries and idempotency. The chosen stack has no message broker, and expected volume is small (≈ hundreds of jobs/hour).

## Decision

Implement a `jobs` table in Postgres consumed by a worker process using `SELECT … FOR UPDATE SKIP LOCKED`, with `idempotency_key` uniqueness, `attempts/max_attempts`, exponential backoff via `run_after`, a **job heartbeat** (the worker refreshes `locked_at` every 30 s while a job runs; a `running` job with no heartbeat for 2 min is requeued), a hard timeout per job kind enforced in the worker (e.g. ingestion 5 min, PDF render 60 s), and graceful shutdown. A fixed visibility timeout was rejected because a job that runs longer than it is picked up a second time while still running, duplicating memory ops, PDFs or model spend. The worker also runs a small set of periodic tasks on fixed intervals (first: `reap_stale_runs` every 30 s, ADR-0006). Driver: psycopg 3.

## Consequences

### Positive
- No extra infrastructure; jobs are enqueued in the same transaction as the data that triggers them
- Easy to inspect and replay with SQL

### Negative
- Polling latency (≈ 1 s) and lower throughput than a dedicated broker
- We own the queue code (small, but must be well-tested for concurrency, including a job that outlives the old 5-minute window)

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
- ADR-0006
