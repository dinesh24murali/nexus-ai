# ADR-0002: LangGraph for Agent Orchestration

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

Nexus AI needs a multi-step agent: tool calling (file search, web search, calculator, memory), human approval before web search that can wait hours and survive restarts, regenerate/cancel, conversation summarisation, and token streaming. Hand-rolling this means building persistence, interrupts and streaming ourselves.

## Decision

We will use **LangGraph (Python)** with `AsyncPostgresSaver` as the checkpointer (`thread_id = conversation_id`) and `AsyncPostgresStore` for long-term memory. The graph is defined in `backend/app/graph/` per `docs/02_outputs/08_ai_system_design.md` §2.

## Consequences

### Positive
- Durable state, `interrupt()`/`Command(resume=…)` and time-travel (regenerate) come built in
- Multiple stream modes (`messages`, `custom`, `updates`) map cleanly onto our SSE events
- Native Langfuse integration through LangChain callbacks

### Negative
- Couples us to LangChain message types and LangGraph's release cadence (pin versions; upgrade deliberately)
- Checkpoint tables grow with every step; need pruning for old threads

### Neutral
- Model calls go through `langchain-openai`, not the raw OpenAI SDK

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| Raw OpenAI SDK + custom loop | Rebuilding persistence, interrupts and streaming is a lot of undifferentiated work |
| OpenAI Agents SDK | Less mature durable HITL and checkpointing; ties orchestration to one provider |
| LlamaIndex workflows | Stronger for RAG pipelines than for stateful agents with HITL |

## References

- `docs/02_outputs/08_ai_system_design.md`
- ADR-0003, ADR-0006
