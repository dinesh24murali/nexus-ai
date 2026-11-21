# Task: 003-plan-revisions

**Status:** done
**Created:** 2026-11-19
**ADR refs:** ADR-0002 … ADR-0007 (revised in place), ADR-0008 (new), ADR-0009 (new)

---

## Goal

Apply the decisions from the 2026-11-19 plan grilling session (Q1–Q31) to the Phase 1 docs, ADRs, memory and quality-gate files, so the plan matches what will actually be built.

## Background

Task 002 generated the Phase 1 docs with many `(default)` intake answers. A grilling session then stress-tested the plan and settled 31 decisions. The baseline before this task is commit `ed74144` ("task 002: Phase 1 docs"), so this task's changes are reviewable as a diff. Docs only; no code.

## Decisions to Apply

| Q | Decision |
|---|----------|
| Q2 | Reference build you run yourself; hosted instance is **invite-only**; engineering gates kept; ops promises optional. G6 dropped |
| Q3 | One developer + AI coding agents, part-time; milestones are ordering, not dates |
| Q4 | Run lease: `active_run_id` + `run_heartbeat_at`; reaper marks stale runs `failed` and writes a `failed` assistant row; graceful drain on shutdown; drop `conversations.summary` (summary lives in graph state) |
| Q5 | Two-tier eval gate: doc-09 numbers are **targets**; `eval-baseline.json` stores **measured** baselines that only ratchet up; absolute gates only for safety (cross-tenant leaks = 0, injection resistance); judged metrics advisory until calibrated |
| Q6 | `remember_fact` taint rule: if untrusted content (`search_my_files`/`web_search` results) entered the context this turn, the call goes through the approval interrupt; otherwise it runs directly |
| Q7 | Chat: M2 eval bake-off `gpt-6.1-terra` (effort `low`, default) vs pinned `gpt-5.4-mini-2026-03-17`; fallback/summarize/title/memory_extract: `gpt-6-luna` (effort `none`); judge: pinned `gpt-5.5-2026-04-23`; reasoning effort per role replaces temperature (temperature only at effort `none`); Responses API everywhere with `store=false`; LLM-T13 for alias-only models = nightly eval + drift alert |
| Q8 | Prompt layout: fixed system prompt → `<summary>` → history → late context message (`<user_memory>`, today's date) → newest user message |
| Q9 | Invite codes: admin CLI creates single-use, expiring codes; `POST /auth/register` requires `invite_code`; `REGISTRATION_MODE=invite\|open` (default `invite`); admin is CLI-only (invites, password reset, disable user) |
| Q10 | Environments: local dev + one prod VM; CI is staging (full compose + Playwright smoke + eval smoke); prod deploy via manually dispatched GitHub Actions workflow |
| Q11 | Langfuse Cloud Hobby by default (dev, CI, prod); self-hosted Langfuse **v4** as optional compose profile; `mask` redaction mandatory |
| Q12 | Keep Qdrant; add one purge test that checks Postgres, checkpoints, store, Qdrant and disk |
| Q13 | Keep account deletion + purge, backups + one restore test, mock-LLM load test (20 streams); defer data export (FR-005) and Prometheus metrics; cut SLO + alert rules (keep `/readyz` uptime check + spend alert) and the real-model load run; short privacy section in README |
| Q14 | Goals: keep G2, G3, G5, G4 (new cost model); G1 advisory; drop G6, G7; add "fresh clone to working stack ≤ 15 min" and "every AI feature ships with its eval gate" |
| Q15 | `web_search` approval stays per call in v1 |
| Q16 | All four pillars in v1; milestones M1 skeleton+invites+deploy+backups live → M2 chat+eval harness → M3 RAG → M4 tools/HITL → M5 memory → M6 hardening; each ends deployed |
| Q17 | Agents draft eval records, developer reviews; v1 datasets halved except `safety.redteam` (40); only the faithfulness judge is calibrated (30 labels); relevance judge advisory; injection judged by deterministic canaries |
| Q18 | Repo-canonical prompts: `backend/prompts/*.md` baked into the image and loaded at runtime; CI pushes to Langfuse on merge (label `production`); traces carry prompt name + version |
| Q19 | Coverage: 80/70 are targets; `tests/coverage-baseline.json` stores measured coverage and ratchets up; reach target by M6 |
| Q20 | `LLM_MODE=fake` in the model factory: scripted responses (incl. tool calls and interrupts) keyed by fixture id, configurable token delay; API refuses to start with `ENV=prod` + `LLM_MODE=fake` |
| Q21 | This task; roadmap groups shift by one (003-skeleton → 004-skeleton … 010-hardening); ADRs 0002–0007 revised in place; new ADR-0008 (repo-canonical prompts) and ADR-0009 (two-tier quality gates); run lease folded into ADR-0006; commit only when the developer says so |
| Q22 | Versions: Python 3.14, FastAPI 0.16x, LangGraph 1.2.x (+ checkpoint-postgres 3.2.x, `LANGGRAPH_STRICT_MSGPACK=true`), langchain-openai 1.8.x, Langfuse SDK v5, Qdrant 1.20 (`query_points`), Postgres 18 (`pgvector/pgvector:pg18`), React 19, Vite 8, Tailwind 4, React Router 8 (SPA mode), Caddy 2.11; docs name majors only, exact pins in lockfiles |
| Q23 | TypeScript stays on 5.9 |
| Q24 | psycopg 3 is the only Postgres driver (`postgresql+psycopg://`) |
| Q25 | Hand-written SSE client (`fetch` + `ReadableStream` + line parser + `AbortController`); drop `@microsoft/fetch-event-source` |
| Q26 | Design system unchanged; tokens move to Tailwind 4 `@theme` |
| Q27 | Per-user daily budget in USD ($0.50 default, env) from `app/llm/pricing.py`; global daily cap ($5 default) blocks chat; keep 30 msg/min |
| Q28 | Moderation stays fail-open (logged + tagged); "revisit" note removed |
| Q29 | Remaining intake defaults accepted as written |
| Q30 | Langfuse Cloud **EU** region |
| Q31 | Baseline committed before this task |

## Acceptance Criteria

- [x] Docs 01–10 reflect every decision above; no remaining references to `gpt-4.1`, temperature-based model config, Langfuse v3, self-hosted-by-default Langfuse, staging VM, `fetch-event-source`, `asyncpg`, token-based daily budgets, open registration by default, or data export as v1 scope
- [x] `04_api_spec.yaml` is valid OpenAPI 3.1 after the changes (invite code on register, USD usage, export endpoint removed)
- [x] ADRs 0002–0007 revised (each with a revision note); ADR-0008 and ADR-0009 created
- [x] `AGENTS.md`, `backend/AGENTS.md`, `frontend/AGENTS.md` versions and §3.8 prompt rule updated
- [x] `memory/` files updated (`project.md`, `ai.md`, `api.md`, `auth.md`, `_index.md` if needed)
- [x] `evals/eval-baseline.json` and `tests/coverage-baseline.json` restructured for the two-tier gate
- [x] Intake answers revised where a decision changed them; `.env.example` updated
- [x] `CHANGELOG.md` entry; task moved to `tasks/done/`

## Dependencies

- **Tasks:** 002-phase1-docs
- **Memory files:** `memory/project.md`, `memory/ai.md`, `memory/api.md`, `memory/auth.md`
- **Docs:** `docs/02_outputs/01`–`10`, `adr/0002`–`0007`

## AI Impact

- **Prompts touched:** none in code; prompt layout (Q8) and source of truth (Q18) change on paper
- **Models / params changed:** all model roles (Q7); temperature → reasoning effort
- **Graph / tools / retrieval changes:** `remember_fact` approval policy (Q6); run lease (Q4)
- **Eval datasets affected / added:** dataset sizes and judge strategy (Q17); baseline file format (Q5)
- **Threat model entries:** LLM-T01, T02, T04, T11, T13 mitigations; tool permission matrix for `remember_fact`
- **Expected cost / latency delta:** cost model re-estimated for the new models (G4)

## Notes

- ADRs 0002–0007 are `Accepted`, and `adr/AGENTS.md` normally forbids editing accepted ADRs. The developer decided (Q21) to revise them in place because no code depends on them yet; the originals are preserved in commit `ed74144`.
- Model and version facts come from web lookups on 2026-11-19; re-check at the M2 bake-off.
