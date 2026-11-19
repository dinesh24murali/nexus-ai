# ADR-0004: Self-Hosted Langfuse for LLM Observability, Prompts and Evals

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

We need traces of every LLM/tool/retrieval step with cost, a prompt registry with versions, datasets and experiment runs for evals, and user-feedback scores. Data includes user PII, so where traces live matters.

## Decision

Use **Langfuse v3, self-hosted** in the same Compose stack (web, worker, ClickHouse, Redis, MinIO; its own logical DB in our Postgres). Use it for: tracing (LangChain callback handler), prompt management (label `production`, with repo fallbacks in `backend/prompts/`), datasets + dataset runs (mirroring `evals/datasets/`), and scores (user feedback, judges). Dev can point at Langfuse Cloud via env.

## Consequences

### Positive
- Open source, data stays on our infra; one tool for traces, prompts, evals and feedback
- First-class LangGraph/LangChain integration

### Negative
- Adds 5 containers and ~2–3 GB RAM (ClickHouse) to the VM
- Langfuse becomes a soft dependency: tracing must be best-effort and prompts need the repo fallback

### Neutral
- Langfuse admin UI must be protected separately (Caddy basic auth + IP allowlist)

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| LangSmith | Managed SaaS and paid; data leaves our infrastructure |
| OpenTelemetry + Grafana only | No prompt registry, datasets or LLM-specific UI |
| Langfuse Cloud for prod | Viable later; self-host chosen for data control |

## References

- `docs/02_outputs/08_ai_system_design.md` §4, §10
- `docs/02_outputs/09_eval_plan.md`
