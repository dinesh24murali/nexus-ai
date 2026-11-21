# ADR-0002: LangGraph for Agent Orchestration

**Status:** Accepted
**Date:** 2026-11-19
**Revised:** 2026-11-19 (task 003: LangGraph 1.x, psycopg 3, run lease, `remember_fact` approval policy); 2026-11-21 (task 003b: `remember_fact` and its taint rule removed; mixed tool calls wait for one approval; tool cap counts individual calls)
**Deciders:** Nexus AI Team

---

## Context

Nexus AI needs a multi-step agent: tool calling (file search, web search, calculator), human approval before web search that can wait hours and survive restarts, regenerate/cancel, conversation summarisation, and token streaming. Hand-rolling this means building persistence, interrupts and streaming ourselves.

## Decision

We will use **LangGraph 1.x (Python)** with `AsyncPostgresSaver` (`langgraph-checkpoint-postgres` 3.x) as the checkpointer (`thread_id = conversation_id`) and `AsyncPostgresStore` for long-term memory. Both use a **psycopg 3** async pool, the same driver as the rest of the app (`postgresql+psycopg://`). `LANGGRAPH_STRICT_MSGPACK=true` is set in every environment. The graph is defined in `backend/app/graph/` per `docs/02_outputs/08_ai_system_design.md` §2.

- The checkpointer is the source of truth for agent state, including the rolling summary; there is no copy of the summary in application tables.
- Runs execute inside the API process and hold a lease (`conversations.active_run_id` + `run_heartbeat_at`); see ADR-0006 for the lease, reaper and graceful drain.
- Tool approval is configured per tool in a `TOOL_POLICY` map: `web_search` always interrupts; other tools never do. When any call in a model message needs approval, the run interrupts before any of that message's calls execute, and all of them run together after the decision. The cap of 6 counts individual calls.
- The agent has no tool that writes long-term memory: memory is written by the `extract_memories` job from the user's own message and, from v1.1, by the `manage_memory` pipeline (ADR-0010), whose forgets always go through an approval interrupt. (Revision 1 had a `remember_fact` tool with a "taint rule"; it was removed in task 003b because v1.1's memory commands made it redundant and its absence makes memory poisoning from tool output structurally impossible.)

## Consequences

### Positive
- Durable state, `interrupt()`/`Command(resume=…)` and time-travel (regenerate) come built in
- Multiple stream modes (`messages`, `custom`, `updates`) map cleanly onto our SSE events
- Native Langfuse integration through LangChain callbacks
- 1.x is the supported line (0.x maintenance ends Dec 2026)

### Negative
- Couples us to LangChain message types and LangGraph's release cadence (pin versions in the lockfile; upgrade deliberately)
- Checkpoint tables grow with every step; need pruning for old threads

### Neutral
- Model calls go through `langchain-openai` (Responses API, ADR-0005), not the raw OpenAI SDK

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| Raw OpenAI SDK + custom loop | Rebuilding persistence, interrupts and streaming is a lot of undifferentiated work |
| OpenAI Agents SDK | Less mature durable HITL and checkpointing; ties orchestration to one provider |
| LlamaIndex workflows | Stronger for RAG pipelines than for stateful agents with HITL |

## References

- `docs/02_outputs/08_ai_system_design.md`
- ADR-0003, ADR-0005, ADR-0006
