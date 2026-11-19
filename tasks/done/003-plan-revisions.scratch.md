# Scratch: 003-plan-revisions

> Decisions Q1–Q31 are listed in `003-plan-revisions.md`. The developer reviewed and confirmed them in the grilling session on 2026-11-19 and asked to start; this scratch file records how they are applied.

---

## Plan

1. **New ADRs:** ADR-0008 repo-canonical prompts (Q18) and ADR-0009 two-tier quality gates (Q5, Q17, Q19).
2. **Revise ADRs 0002–0007 in place**, each with a `Revised: 2026-11-19 (task 003)` line:
   - 0002: LangGraph 1.x, psycopg 3, strict msgpack
   - 0003: Postgres 18 / pgvector pg18, Qdrant 1.x `query_points`, the cross-store purge test
   - 0004: Langfuse Cloud (EU, Hobby) by default, self-hosted v4 optional, SDK v5, prompts no longer canonical in Langfuse
   - 0005: new model roles, reasoning effort instead of temperature, Responses API with `store=false`, `LLM_MODE=fake`
   - 0006: hand-written SSE client, run lease + reaper + graceful drain
   - 0007: unchanged except references (reaper job)
3. **Doc 01 PRD:** purpose (reference build, invite-only), goals (Q14), FR-001 invite codes, FR-005 deferred, FR-051/053 USD budgets, NFRs (no SLO), assumptions, open questions resolved, timeline (Q16).
4. **Doc 02 functional spec:** register flow with invite code, export removed, usage in USD, run lease/failed states, `remember_fact` approval, permissions, retention.
5. **Doc 03 architecture:** diagram without in-stack Langfuse, versions, psycopg, `conversations` columns (drop `summary`, add `run_heartbeat_at`), `invites` table, envs (dev + prod, CI = staging), observability (no SLO alerts), Postgres 18.
6. **Doc 04 API spec:** `invite_code` on register, `INVITE_INVALID` error, remove `/me/export`, `Usage` in USD, staging server removed, budget wording; validate with Redocly.
7. **Doc 05 implementation plan:** roadmap groups 004–010, milestones re-ordered deploy-first, TASK changes (invites, fake LLM, run lease, Langfuse Cloud, prompt sync, two-tier gates, versions), deploy/backups moved to Phase 0/1, ops tasks trimmed.
8. **Doc 06 dev setup:** versions, Langfuse Cloud keys, `LLM_MODE=fake`, invite CLI, CI = staging, prod deploy.
9. **Doc 07 design system:** §4b Tailwind 4 `@theme` instead of `tailwind.config.ts`.
10. **Doc 08 AI design:** model table, prompt layout, prompt catalog source, `remember_fact` taint rule, run lease, Responses API, budgets, Langfuse SDK v5 notes.
11. **Doc 09 eval plan:** two-tier gate, dataset sizes, judge model + calibration, canary-based injection metric, CI (no staging), cost metric.
12. **Doc 10 threat model:** T02/T04 taint rule, T11 USD budgets, T13 alias-only models, permission matrix, Langfuse Cloud as third-party processor, kill switches.
13. **Framework files:** `AGENTS.md` §1 + §3.8, `backend/AGENTS.md`, `frontend/AGENTS.md`, `evals/AGENTS.md`, `tests/AGENTS.md` (load the writing-for-agents skill first).
14. **Gate files:** `evals/eval-baseline.json` (`targets` + empty-until-measured `metrics`; compatible with `check_baseline.py` without code changes), `tests/coverage-baseline.json` (`targets`).
15. **Memory:** `project.md`, `ai.md`, `api.md`, `auth.md`.
16. **Intake, `.env.example`, README privacy section, CHANGELOG**; grep for stale terms; move task to done (no commit).

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| adr/0008-repo-canonical-prompts.md | create | Q18 |
| adr/0009-two-tier-quality-gates.md | create | Q5, Q17, Q19 |
| adr/0002…0007 | modify | revised in place (Q21) |
| docs/02_outputs/01_prd.md … 10_llm_threat_model.md | modify | per plan above |
| AGENTS.md, backend/AGENTS.md, frontend/AGENTS.md, evals/AGENTS.md, tests/AGENTS.md | modify | versions, prompt rule, gate rules |
| evals/eval-baseline.json, tests/coverage-baseline.json | modify | two-tier format |
| memory/project.md, ai.md, api.md, auth.md | modify | |
| docs/00_intake/intake_questionnaire.md | modify | revised answers marked `(revised 2026-11-19)` |
| .env.example, README.md, CHANGELOG.md | modify | |

## AI Impact & Eval Plan

- Paper-only changes to models, prompt layout, tool policy and the eval gate; no evals to run (no suite exists yet).
- `eval-baseline.json` keeps `check_baseline.py` working: `metrics` stays empty until the first measured run, so `make eval` is a no-op gate until task 006 (eval harness).

## Uncertainties

- Model facts (names, prices, temperature support, snapshot availability) and library versions come from web lookups on 2026-11-19; recorded as such.
- Responses API with `store=false` likely needs encrypted reasoning items carried in checkpoint state; recorded as a risk for TASK-009/010, not resolved here.
- Langfuse SDK v4+ filters non-LLM spans by default; per-node spans need a check in TASK-009.

## What Will Be Skipped

- No code, compose files or CI workflows (task 004+).
- `evals/check_baseline.py` unchanged; the two-tier format is expressed with keys it already ignores (`targets`).
- `docs/01_prompts/*` and `07_design_system.template.md` stay untouched (framework templates).

## Risks

- Inconsistencies between docs after many edits → finish with a grep for stale terms (`gpt-4.1`, `temperature`, `staging`, `fetch-event-source`, `asyncpg`, `200k`, `export`, `v3`, `React 18`, `tailwind.config`) and re-validate the OpenAPI spec.
- ADRs edited in place against the `adr/AGENTS.md` rule → explicit developer decision (Q21); originals in `ed74144`.
