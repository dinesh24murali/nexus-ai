# ADR-0008: Prompts Are Canonical in the Repo

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

The Phase 1 plan made Langfuse the prompt registry (label `production`) with a fallback copy in `backend/prompts/`. That gives two editable copies of every prompt. Evals run against one copy while prod may serve the other, and code changes made by AI coding agents only touch the repo. With Langfuse moving to Langfuse Cloud (ADR-0004), reading prompts from it at runtime also adds a network dependency on a third party to every run.

## Decision

- `backend/prompts/<name>.md` is the **only** editable source of every prompt. Each file has front-matter with `name` and `version` (integer, bumped on every change).
- Prompts are baked into the image and loaded from disk at runtime. The app never fetches prompts from Langfuse.
- On merge to `main`, CI pushes each changed prompt to Langfuse as a new version labelled `production`, so traces and dataset runs can be filtered and compared by prompt version.
- Every generation is traced with the prompt `name` and `version` as metadata.
- Editing prompts in the Langfuse UI is allowed only for throwaway experiments; a winning change is copied back into the repo and goes through `make eval` like any other change.

## Consequences

### Positive
- What the eval suite tests is exactly what ships; prompt changes are reviewed in PRs with diffs
- No runtime dependency on Langfuse for prompts; startup and runs work when Langfuse is down
- AI coding agents can change prompts with the same workflow as code

### Negative
- Changing a prompt in prod requires a deploy (no hot-swap from the Langfuse UI)
- A small CI sync step to maintain

### Neutral
- Langfuse still holds a version history of prompts, but it is a mirror

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| Langfuse canonical, repo fallback (original plan) | Two editable copies drift; evals and prod can disagree |
| Langfuse only, no repo copy | Prompt changes bypass code review and the eval gate; hard for AI agents to edit |

## References

- `AGENTS.md` §3.8
- `docs/02_outputs/08_ai_system_design.md` §4
- ADR-0004
