# ADR-0003: Qdrant for Vectors, PostgreSQL for Everything Else

**Status:** Accepted
**Date:** 2026-11-19
**Revised:** 2026-11-19 (task 003: Postgres 18, Qdrant 1.x `query_points`, cross-store purge test)
**Deciders:** Nexus AI Team

---

## Context

We need relational storage (users, invites, conversations, messages, files, jobs), LangGraph checkpoints and memory store, and a vector index over document chunks with strict per-user filtering. The developer chose Qdrant and PostgreSQL. At invite-only scale pgvector alone would be enough; Qdrant is kept deliberately because the project is a reference build and Qdrant shows patterns worth demonstrating (tenant-indexed payloads, alias-based re-indexing, hybrid search later).

## Decision

- **PostgreSQL 18** (`pgvector/pgvector:pg18` image, pgvector 0.8.x): application tables, LangGraph checkpointer and store (memories, with its pgvector-backed index), job queue. Driver: psycopg 3.
- **Qdrant 1.x** (1.20 at time of writing): a single collection `documents` (1536-d, cosine) with `user_id` as an indexed tenant payload (`is_tenant=true`) and `file_id` indexed. Point ids `uuid5(file_id, chunk_index)` keep ingestion idempotent. Re-indexing uses versioned collections behind an alias. Queries use `query_points` (the client's `search` method was removed in 1.16).
- **One cross-store purge test** runs `purge_user` and asserts zero rows, checkpoints, store items, Qdrant points and files remain for the user.

## Consequences

### Positive
- Qdrant gives fast filtered search, payload indexes, snapshots, and sparse vectors (hybrid search later)
- Memories stay transactional with the rest of user data in Postgres

### Negative
- Two stores to back up and keep consistent (file delete must remove Qdrant points first; purge job covers both; the purge test guards it)
- The Postgres store needs the `pgvector` extension (use the `pgvector/pgvector` image)

### Neutral
- One collection for all tenants (payload filtering) instead of a collection per user
- Postgres 18 images keep data under `/var/lib/postgresql` (volume mount path differs from ≤ 17)

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| pgvector for document chunks | Would be enough at invite-only scale; Qdrant kept for its reference value, filtered-ANN performance and hybrid search |
| Collection per user | Doesn't scale operationally; Qdrant recommends payload-based multitenancy |

## References

- `docs/02_outputs/03_tech_architecture.md` §3
- `docs/02_outputs/10_llm_threat_model.md` LLM-T05
