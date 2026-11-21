# ADR-0010: Intent Router in Front of Per-Intent Subgraphs

**Status:** Accepted
**Date:** 2026-11-21
**Deciders:** Nexus AI Team
**Supersedes:** the "no pre-router" note in doc 08 §13 GAP-06

---

## Context

In v1 one chat agent receives every message and picks among 4 tools by native function calling. v1.1 adds capabilities that are **pipelines, not tools**: PDF documents, deep research, scheduled tasks, memory management by chat, and a cheap fast path for greetings and help. They differ in model, reasoning effort, approval policy, budget, output type (chat text, PDF file, a stored schedule) and latency. Binding them all to one agent as tools would mean one prompt carrying every policy, one model tier for every request, and approval rules that depend on how the model chains calls.

## Decision

Add a `route` node before the agent. It is a `gpt-6-luna` call (effort `none`) with strict structured output `RouteDecision {intent, confidence, slots}`, and it dispatches to one LangGraph subgraph per intent: `general_chat` (today's agent, unchanged), `smalltalk_help`, `manage_memory`, `generate_document`, `deep_research`, `schedule_task`.

- **Safe fallback:** confidence below 0.70, a router error, a timeout (5 s) or an unknown intent all go to `general_chat`. Failures cost quality, never availability.
- **Explicit mode skips the router:** slash commands (`/pdf`, `/research`, `/schedule`, `/memory`) send `mode` in the request.
- **Composition through slots, not multi-routing:** one intent per message. Compound requests go to the intent that owns the final deliverable (`schedule_task` > `generate_document` > `deep_research`), with the rest as slots (e.g. `deep_research` + `output: pdf`).
- **Router input is user-authored text only** (the last 3 user messages + the previous intent), never tool output or documents, so injected content cannot steer routing (LLM-T17).
- The router never grants permissions: side-effect intents keep their own approval interrupts.

## Consequences

### Positive
- Each pipeline has its own prompt, model tier, budget and eval suite. Routing quality is measured directly (`router.*` metrics, confusion matrix).
- Cheap paths get cheaper: greetings and memory commands skip the chat model.

### Negative
- An extra model call on every turn: about 150–300 ms and ~$0.00005. It runs in parallel with `load_context` to hide part of the latency.
- Misroutes are a new failure class, and they need a labelled dataset (`router.intents`, 120 cases) that must be maintained.

### Neutral
- `ROUTER_DISABLED=1` sends everything to `general_chat`, which is exactly v1 behaviour.
- An embedding-similarity pre-filter (semantic router) can be added later in front of the LLM router if latency or cost demands it.

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| All capabilities as tools on the one agent | Mixes approval, budget and model-tier policies in one prompt; can't put cheap paths on a cheaper model; routing is hard to evaluate separately |
| Embedding-only semantic router | No slot extraction (topic, cadence, output format); weak on follow-ups ("make that a PDF") |
| Supervisor agent that calls sub-agents as tools | Two chat-model calls per turn; harder to bound cost and to test deterministically |

## References

- `docs/02_outputs/08_ai_system_design.md` §2.2, §2.5, §3
- `docs/02_outputs/09_eval_plan.md` §3 (`router.*`)
- `docs/02_outputs/10_llm_threat_model.md` LLM-T17
