# ADR-0010: Intent Router in Front of Per-Intent Subgraphs

**Status:** Accepted
**Date:** 2026-11-21
**Revised:** 2026-11-21 (task 003b: 4 intents — `smalltalk_help` cut, `schedule_task` moved to the backlog; slash commands parsed without a model; tuned confidence threshold; router latency counted in TTFT)
**Deciders:** Nexus AI Team
**Supersedes:** the "no pre-router" note in doc 08 GAP-06 (revision 1.2; gaps resolved in task 003b)

---

## Context

In v1 one chat agent receives every message and picks among 4 tools by native function calling. v1.1 adds capabilities that are **pipelines, not tools**: PDF documents, deep research and memory management by chat. They differ in model, reasoning effort, approval policy, budget, output type (chat text or a PDF file) and latency. Binding them all to one agent as tools would mean one prompt carrying every policy, one model tier for every request, and approval rules that depend on how the model chains calls.

## Decision

Add a `route` node before the agent. It is a `gpt-6-luna` call (effort `none`) with strict structured output `RouteDecision {intent, confidence, slots}`, and it dispatches to one LangGraph subgraph per intent: `general_chat` (today's agent), `manage_memory`, `generate_document`, `deep_research`.

- **Safe fallback:** confidence below `min_confidence`, a router error, a timeout (5 s) or an unknown intent all go to `general_chat`. Failures cost quality, never availability.
- **Tuned threshold:** the router's confidence is self-reported, so `min_confidence` (in the `nexus.route.md` front-matter, starting at 0.70) is set from the first reviewed `router.intents` run for the best intent accuracy and re-tuned when the router prompt or model changes. If the number turns out to be uninformative (> 90% of cases ≥ 0.9, or wrong routes not scoring lower than correct ones), the threshold is dropped and only errors fall back.
- **Slash commands skip the router:** the API parses a command at the start of the message without a model (`/pdf`, `/pdf last`, `/brief <topic>`, `/research <question>`, `/memory`, `/memory remember|forget <text>`, `/help`), so a user can always force a pipeline and its slots. `/help` is fixed text and replaces a separate small-talk intent; "what can you do?" is answered by `general_chat` from a capability paragraph in `nexus.system`.
- **Composition through slots, not multi-routing:** one intent per message. Compound requests go to the intent that owns the final deliverable (`generate_document` > `deep_research`), with the rest as slots (e.g. `deep_research` + `output: pdf`).
- **Latency is on the hot path:** dispatch waits for the router, so G3 (TTFT) is measured end to end with it. If TTFT misses, a keyword pre-gate in front of the router (only candidate messages are classified) is the planned fix.
- **Router input is user-authored text only** (the last 3 user messages + the previous intent), never tool output or documents, so injected content cannot steer routing (LLM-T17).
- The router never grants permissions: side-effect intents keep their own approval interrupts (the research plan, and every memory forget).

## Consequences

### Positive
- Each pipeline has its own prompt, model tier, budget and eval suite. Routing quality is measured directly (`router.*` metrics, confusion matrix).
- Memory commands skip the chat model; `/help` and `/pdf last` need no model at all.

### Negative
- An extra model call on every turn: about 150–300 ms and ~$0.00009. It runs in parallel with `load_context` to hide part of the latency, but what remains counts toward TTFT.
- Misroutes are a new failure class, and they need a labelled dataset (`router.intents`, 120 cases) that must be maintained.
- A reply like "yes, do it" to an assistant offer can't be routed, because the router sees only user messages; `nexus.system` therefore tells the chat model never to offer a PDF or research itself.

### Neutral
- `ROUTER_DISABLED=1` sends everything to `general_chat`, which is exactly v1 behaviour.
- An embedding-similarity pre-filter (semantic router) can be added later in front of the LLM router if latency or cost demands it.

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| All capabilities as tools on the one agent | Mixes approval, budget and model-tier policies in one prompt; can't put cheap paths on a cheaper model; routing is hard to evaluate separately |
| Embedding-only semantic router | No slot extraction (topic, cadence, output format); weak on follow-ups ("make that a PDF") |
| Supervisor agent that calls sub-agents as tools | Two chat-model calls per turn; harder to bound cost and to test deterministically |
| Slash commands only, no router | No misroutes and no router dataset, but no natural-language "make that a PDF"; kept as the always-available override instead |
| A `smalltalk_help` intent on the router model (revision 1) | Saves a fraction of a cent per greeting, but adds a subgraph, a prompt, an eval slice and the fuzziest misroute boundary |
| A `schedule_task` intent (revision 1) | With in-app delivery only, a schedule saves one message, at the cost of unattended runs, timezone handling and budget contention; moved to the PRD backlog until email/push delivery exists |

## References

- `docs/02_outputs/08_ai_system_design.md` §2.2, §2.5, §2.6, §3
- `docs/02_outputs/09_eval_plan.md` §3 (`router.*`)
- `docs/02_outputs/10_llm_threat_model.md` LLM-T17
