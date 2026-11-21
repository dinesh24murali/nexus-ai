# Scratch: 002-phase1-docs

## Plan

1. Fill intake with developer's stack + defaults (flagged `(default)`).
2. Replace placeholders in project files (not in `docs/01_prompts/`, `docs/02_outputs/*.template.md` or the AGENTS §6 reference table).
3. Generate 01 PRD → 02 functional spec → 03 architecture → 04 OpenAPI YAML (validate) → 05 implementation plan → 06 dev setup → 07 design system (compute WCAG contrast programmatically) → 08 AI design → 09 eval plan → 10 threat model.
4. Write ADRs 0002–0007.
5. Seed memory files and eval baseline.
6. CHANGELOG, move task.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| docs/00_intake/intake_questionnaire.md | modify | answers |
| docs/02_outputs/01..10 | create | |
| adr/0002..0007 | create | |
| memory/*.md | modify/create | |
| AGENTS.md, backend/frontend AGENTS.md, .env.example, Makefile, adr/0001, tasks/done/000 | modify | placeholders |
| evals/eval-baseline.json | modify | thresholds |

## AI Impact & Eval Plan

- Defines prompts, models, tools and thresholds on paper only; no code.

## Uncertainties

- OpenAI model names change often; they are env-configurable and flagged as an open question.
- Staging/prod URLs unknown → `TBD` (kept as open question).

## What Will Be Skipped

- Code, CI workflow files, docker-compose (task 003).

## Risks

- Docs-only; inconsistencies between docs are the main risk → API spec is validated and is the reference for endpoint names.
