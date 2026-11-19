# ADR-0003: Qdrant for Vectors, PostgreSQL for Everything Else

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

We need relational storage (users, conversations, messages, files, jobs), LangGraph checkpoints and memory store, and a vector index over document chunks with strict per-user filtering. The developer chose Qdrant and PostgreSQL.

## Decision

- **PostgreSQL 16:** application tables, LangGraph checkpointer and store (memories, with its pgvector-backed index), job queue.
- **Qdrant:** a single collection `documents` (1536-d, cosine) with `user_id` as an indexed tenant payload (`is_tenant=true`) and `file_id` indexed. Point ids `uuid5(file_id, chunk_index)` keep ingestion idempotent. Re-indexing uses versioned collections behind an alias.

## Consequences

### Positive
- Qdrant gives fast filtered search, payload indexes, snapshots, and sparse vectors (hybrid search later)
- Memories stay transactional with the rest of user data in Postgres

### Negative
- Two stores to back up and keep consistent (file delete must remove Qdrant points first; purge job covers both)
- The Postgres store needs the `pgvector` extension (use the `pgvector/pgvector:pg16` image)

### Neutral
- One collection for all tenants (payload filtering) instead of a collection per user

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| pgvector for document chunks | Developer preference for Qdrant; Qdrant has better filtered-ANN performance and hybrid search |
| Collection per user | Doesn't scale operationally; Qdrant recommends payload-based multitenancy |

## References

- `docs/02_outputs/03_tech_architecture.md` §3
- `docs/02_outputs/10_llm_threat_model.md` LLM-T05
