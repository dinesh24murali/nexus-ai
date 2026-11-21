# Task: 002-phase1-docs

**Status:** done
**Created:** 2026-11-19
**ADR refs:** ADR-0001, ADR-0002 … ADR-0007

---

## Goal

Complete Phase 1 for Nexus AI: fill the intake with agreed defaults, replace project placeholders, generate docs 01–10 into `docs/02_outputs/`, record the stack ADRs and seed `memory/` and `evals/`.

## Background

Task 001 added the AI-engineering layer. The stack (FastAPI, LangGraph, React, PostgreSQL, Qdrant, Langfuse, OpenAI) and v1 features (core chat, RAG, tools + HITL, long-term memory) were chosen by the developer; everything else uses sensible defaults the developer asked for and can revise.

## Acceptance Criteria

- [x] `docs/00_intake/intake_questionnaire.md` fully filled; defaults marked `(default)`
- [x] Project-level placeholders replaced (AGENTS files, memory, ADR-0001, .env.example, Makefile); prompt templates left untouched
- [x] `docs/02_outputs/` contains 01–10 (04 as `04_api_spec.yaml`, valid OpenAPI 3.1)
- [x] ADRs 0002–0007 written
- [x] `memory/project.md`, `memory/ai.md` seeded; `memory/api.md`, `memory/auth.md` created (backend AGENTS requires them)
- [x] `evals/eval-baseline.json` seeded from the eval plan

## AI Impact

- **Prompts touched:** none in code; prompt catalog defined in 08
- **Eval datasets affected / added:** baseline thresholds only; datasets come in task 005

## Dependencies

- **Tasks:** 001-ai-framework-layer
- **Memory files:** `memory/project.md`, `memory/ai.md`

## Notes

Docs are written to be concise but complete; target lengths in the prompts are upper guidance.
