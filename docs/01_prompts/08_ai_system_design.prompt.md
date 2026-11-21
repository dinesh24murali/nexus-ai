# Prompt: Generate AI System Design Document

> **How to use:** Complete Prompts 01–03 first. Fill in every `[[PLACEHOLDER]]` from intake §11, then paste this entire file (plus the PRD and architecture doc) into your AI tool. Save the output to `docs/02_outputs/08_ai_system_design.md`.

---

## Instructions to AI

You are a staff AI engineer. Design the **AI subsystem** of the project below: how requests flow through the LLM orchestration layer, which tools and data it touches, how state and memory are kept, and how responses are streamed. Make concrete, opinionated choices (models, chunk sizes, top-k, timeouts). Do not hedge — pick one and justify it. Everything you design must be traceable, evaluable and safe for multi-tenant use.

---

## Project Input

**Project name:** [[PROJECT_NAME]]
**LLM provider:** [[LLM_PROVIDER]]
**Orchestration framework:** [[AI_ORCHESTRATION]]
**Vector DB:** [[VECTOR_DB]]
**Relational DB:** [[DATABASE]]
**Observability:** [[LLM_OBSERVABILITY]]
**Models and roles:** [[Fill from intake 11.2]]
**AI features in v1:** [[Fill from intake 11.4]]
**Knowledge sources:** [[Fill from intake 11.5]]
**Latency / cost budgets:** [[Fill from intake 11.7–11.8]]
**Guardrails:** [[Fill from intake 11.10]]

---

## Required Sections

1. **AI Request Lifecycle** — Mermaid sequence diagram from user message to final streamed token, including auth, moderation, graph run, tool calls, persistence and tracing
2. **Orchestration Graph**
   - State schema (field names, types, reducers)
   - Nodes and edges as a Mermaid flowchart; conditional edges with their routing conditions
   - Persistence: checkpointer choice, thread-id strategy, how regenerate/edit/resume work
   - Interrupts / human-in-the-loop points and the resume payload shape
3. **Model Strategy** — model per role, temperature, max tokens, timeouts, retries, fallback model, structured-output usage
4. **Prompt Catalog** — table of every prompt: name, purpose, input variables, output format, owning node. Prompts are stored in the prompt registry ([[LLM_OBSERVABILITY]]) with a version-pinned fallback in the repo
5. **Tool Catalog** — for each tool: name, description shown to the model, argument schema, side effects, auth scope, timeout, whether it needs human approval, and how its output is marked as untrusted
6. **RAG Pipeline** (if applicable)
   - Ingestion: parsing, chunking (size/overlap/strategy), metadata, embedding model, idempotency, re-index strategy
   - Retrieval: filters (tenant isolation!), top-k, hybrid/MMR/rerank decision, context-window budget
   - Citation format returned to the client
7. **Memory Design** — short-term (conversation window, summarization trigger) and long-term (what is extracted, schema, namespace, when written, how users view/delete it)
8. **Streaming Protocol** — transport (SSE/WebSocket), event types with JSON payload schemas, cancellation, error events, reconnection
9. **Background Jobs** — anything taken off the request path (ingestion, titles, memory extraction): queue choice, retries, idempotency keys
10. **Observability & Cost** — trace structure (trace/span/generation naming), tags and metadata, PII handling, token/cost accounting per user and per message, user feedback capture
11. **Failure Modes** — table of failure → detection → user-visible behaviour (provider outage, rate limit, tool timeout, context overflow, empty retrieval, malformed tool args)
12. **Eval Hooks** — which components need offline evals and what golden data they need (input for Prompt 09)

---

## Output Format

- Use Markdown with Mermaid diagrams
- Include JSON schemas for state, tool args and stream events
- Be specific with versions and numbers
- Target length: 2500–4500 words
