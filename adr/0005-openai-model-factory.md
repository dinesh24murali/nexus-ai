# ADR-0005: OpenAI Behind a Single Model Factory

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

v1 uses OpenAI only (developer decision). Model calls need consistent timeouts, retries, fallbacks, tracing callbacks, cost accounting, and the ability to swap models (or providers later) without touching graph code. OpenAI model names change frequently.

## Decision

All chat/embedding/moderation clients are created by `backend/app/llm/factory.py` (`get_model(role)`, `get_embeddings()`), configured from env (`LLM_CHAT_MODEL`, `LLM_FAST_MODEL`, `LLM_EMBEDDING_MODEL`, …). Roles: `chat`, `summarize`, `title`, `memory_extract`, `judge`. Defaults: `gpt-4.1` (fallback `gpt-4.1-mini`), `gpt-4.1-mini`, `text-embedding-3-small`, `omni-moderation-latest`. No other module instantiates provider clients (enforced by a ruff banned-import rule).

## Consequences

### Positive
- One place for reliability policy, tracing and pricing
- Model swaps are config + `make eval`, not code changes; a provider swap is a factory change

### Negative
- Model names must be re-verified before launch (open question in the PRD)

### Neutral
- Embedding model changes require re-indexing (versioned Qdrant collection, ADR-0003)

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| LiteLLM proxy for multi-provider | Out of scope for v1; adds a service |
| Direct client construction per module | Inconsistent timeouts/tracing; hard to swap models |

## References

- `docs/02_outputs/08_ai_system_design.md` §3
