# Task: 001-ai-framework-layer

**Status:** done
**Created:** 2026-11-18
**ADR refs:** ADR-0001

---

## Goal

Extend the vibecode framework with a reusable AI-engineering layer (intake, prompts, eval gate, AI rules, AI memory) so that LLM applications can be specified, built and verified with the same discipline as plain web apps.

## Background

The framework currently covers classic web apps only. AI apps need extra artifacts: an AI system design (graph/agents, tools, RAG, memory), an eval plan with regression thresholds, an LLM threat model, prompt versioning, and tracing/cost rules. Nexus AI (a ChatGPT-style app on FastAPI + LangGraph + React + Postgres + Qdrant + Langfuse + OpenAI) will be the first consumer of this layer.

## Acceptance Criteria

- [x] Intake questionnaire has an "AI / LLM" section
- [x] Prompts 08 (AI system design), 09 (eval plan), 10 (LLM threat model) exist in `docs/01_prompts/`
- [x] `evals/` folder exists with `AGENTS.md`, `datasets/`, `eval-baseline.json` and a stack-agnostic baseline checker
- [x] `AGENTS.md` has §3.8 AI rules; backend/frontend AGENTS.md updated
- [x] `memory/ai.md` exists and is indexed
- [x] Task + scratch templates have an "AI impact" section
- [x] Makefile has `up`, `down`, `eval`, `eval-smoke` targets; `.env.example` has AI vars

## Dependencies

- **Tasks:** none
- **Memory files:** `memory/project.md`
- **Docs:** none

## Files Expected to Change

See scratch file.

## Notes

Keep the layer stack-agnostic where possible (placeholders), with Nexus AI choices only as examples.
