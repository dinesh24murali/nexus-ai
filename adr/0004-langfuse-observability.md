# ADR-0004: Langfuse for LLM Observability and Evals (Cloud by Default)

**Status:** Accepted
**Date:** 2026-11-19
**Revised:** 2026-11-19 (task 003: Langfuse Cloud EU by default, self-hosted v4 optional, SDK v5, prompts canonical in the repo)
**Deciders:** Nexus AI Team

---

## Context

We need traces of every LLM/tool/retrieval step with cost, datasets and experiment runs for evals, and user-feedback scores. Data includes user PII, so where traces live matters.

The original decision was self-hosted Langfuse v3 in the same Compose stack. Two facts changed it:
- Langfuse v4 is current; self-hosted v3 only receives security patches until the end of January 2027.
- The Langfuse Docker Compose guide recommends at least 4 cores and 16 GiB of memory for Langfuse alone (web, worker, ClickHouse, Redis, S3/MinIO, Postgres), while the planned prod VM is 4 vCPU / 8 GB shared with the whole app.

Nexus AI is a reference build with an invite-only hosted instance, so trace volume is small.

## Decision

- **Default: Langfuse Cloud, EU region, Hobby plan** (free: 50k units/month, 30 days of data access, 2 seats) for dev, CI and the hosted instance. Separate Langfuse projects per environment.
- **Optional: self-hosted Langfuse v4** as a Compose profile (`--profile langfuse`) for self-hosters who want traces on their own infrastructure. It needs its own resources (≥ 4 cores / 16 GiB) and is not part of the default stack.
- **SDK:** Langfuse Python SDK v5 (needs a Langfuse server ≥ 4.59; Cloud qualifies).
- **Uses:** tracing (LangChain callback handler), datasets + dataset runs (mirroring `evals/datasets/`), scores (user feedback, judges), and a **mirror** of prompt versions. Prompts are canonical in the repo (ADR-0008).
- **Redaction is mandatory:** traces leave our infrastructure, so the `mask` function (emails, phone numbers, card/IBAN-like numbers, JWTs, API keys) runs on every export and has unit tests; user ids are HMAC-hashed.

## Consequences

### Positive
- No heavy observability stack on the app VM; `make up` stays small and fast
- Same tool for traces, datasets, scores and feedback; first-class LangGraph/LangChain integration
- Self-hosting remains possible for users who need it

### Negative
- Redacted trace content is stored by a third party (Langfuse, EU region); this must be stated in the privacy notice
- Hobby limits (50k units/month, 30-day retention) cap trace volume; eval runs also consume units
- Langfuse is a soft dependency: tracing must be best-effort and never block a run

### Neutral
- The SDK v4+ filters non-LLM spans by default; per-node spans need explicit configuration (checked in TASK-009)

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| Self-hosted v3 on the app VM (original) | v3 near end of support; does not fit in 8 GB alongside the app |
| Self-hosted v4 on a 16 GB+ VM | More cost and ops for a reference build with a handful of users; kept as an optional profile |
| LangSmith | Paid SaaS; no self-host option for users who want one |
| OpenTelemetry + Grafana only | No datasets, scores or LLM-specific UI |

## References

- `docs/02_outputs/08_ai_system_design.md` §10
- `docs/02_outputs/09_eval_plan.md`
- ADR-0008
