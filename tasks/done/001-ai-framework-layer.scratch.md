# Scratch: 001-ai-framework-layer

## Plan

1. Add intake §11 "AI / LLM" (provider, models, features, knowledge sources, budgets, eval targets, guardrails, observability).
2. Add prompts 08/09/10 following the format of existing prompts (How to use → Instructions → Input → Required Sections → Output Format).
3. Create `evals/` (AGENTS.md, datasets/, results/ gitignored, eval-baseline.json, check_baseline.py using stdlib only).
4. Add AGENTS.md §3.8 (AI changes), §3.7 SSE exception, new placeholders, file map rows; update backend/frontend AGENTS.md.
5. Create `memory/ai.md`, index it.
6. Add "AI Impact" section to task + scratch templates.
7. Makefile: `up`, `down`, `eval`, `eval-smoke`; `.env.example`: AI vars.
8. CHANGELOG entry; move task to done.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| docs/00_intake/intake_questionnaire.md | modify | §11 AI / LLM |
| docs/01_prompts/08_ai_system_design.prompt.md | create | |
| docs/01_prompts/09_eval_plan.prompt.md | create | |
| docs/01_prompts/10_llm_threat_model.prompt.md | create | |
| docs/AGENTS.md | modify | ordering 01 → 10 |
| evals/* | create | gate mirrors coverage-baseline |
| AGENTS.md, backend/AGENTS.md, frontend/AGENTS.md | modify | AI rules |
| memory/ai.md, memory/_index.md | create/modify | |
| tasks/_template.md, tasks/_scratch_template.md | modify | AI impact |
| Makefile, .env.example, .gitignore | modify | |
| CHANGELOG.md | modify | |

## Uncertainties

- The actual eval suite command depends on backend layout (task 005). Makefile takes it via `EVAL_COMMAND` variable; the gate script only compares a results JSON against the baseline.

## What Will Be Skipped

- Filling intake answers / generating outputs (task 002).
- Real eval suite implementation (task 005).

## Risks

- Docs-only change; low blast radius.
