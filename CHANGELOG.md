# Changelog

All notable changes to this project will be documented here.

**AI tools: update this file on every task completion.**
Format: `- [YYYY-MM-DD] <task-name>: <one-line summary>`

---

## Unreleased

<!-- AI: add entries here as tasks are completed -->
- [2026-11-21] 005-core-chat (TASK-012, TASK-011): Postgres job queue (SKIP LOCKED claim, owner-conditional transitions, backoff, idempotent enqueue, per-job heartbeat, per-kind hard timeout, graceful shutdown that refunds the attempt) with a 4-slot worker and periodic tasks (`reap_stale_runs`, `requeue_stale_jobs`, `cleanup_invites`, `cleanup_rate_limits`); `generate_title` job + `nexus.title` v1; streaming chat: `POST /conversations/{id}/messages`, `/regenerate`, `/runs/cancel` and `GET …/messages` over SSE with a run lease, 10 s heartbeat, detached runs (a disconnect never stops one), cancel keeping the partial answer, regenerate by forking the checkpoint, graceful drain (503 `SERVICE_RESTARTING`, 90 s) with a SIGTERM hook and uvicorn `--timeout-graceful-shutdown 105`; per-run usage collector (all model calls, priced per model) and `usage_daily` upsert; deleting a conversation now cancels its run; `LLM_FAKE_TOKEN_DELAY_S`; spec 1.3.2 (`listMessages` 422) and regenerated client; **fix:** NUL in `display_name`/email returned 500 (found by the wider schemathesis scope), now 422; coverage gates `app/jobs` 96, `app/services` 90. No eval gate yet (harness is TASK-015); `Idempotency-Key` on send is accepted but not honoured (open decision)
- [2026-11-21] 005-core-chat (TASK-009, TASK-010): model factory with per-role policy (Responses API, `store=false`, encrypted reasoning for tool loops, temperature only at effort `none`, ruff ban on ad-hoc provider clients), scripted fake model, prompt loader + Langfuse sync (`nexus.system|context|summarize` v1), Langfuse v5 tracing with masking and hashed user ids, pricing; tool-less chat graph (`load_context → summarize? → agent`) with cache-friendly prompt layout, summarisation at 24k tokens, Postgres checkpointer test; Dockerfile ships prompts and bakes the tiktoken cache; coverage gates for `app/llm` and `app/graph`. No eval gate yet (harness is TASK-015)
- [2026-11-21] 003b-plan-revisions-v1.1: docs: applied the second grilling session (Q1–Q18) — v1.1 trimmed to router + memory by chat + PDFs + deep research (schedules → PRD backlog, `smalltalk_help` → `/help` + capability paragraph); deterministic slash-command grammar (`mode` removed from the API); router confidence threshold `min_confidence` tuned on eval data; `last_answer` document source; per-run cost caps by construction (≤ $0.05); `remember_fact` + taint rule removed from v1, every memory forget confirmed (`forget_memory`); regenerate only on chat answers; doc 08 §13 gaps GAP-01…09 resolved in place; job heartbeat (ADR-0007); kill switches in a `runtime_flags` table; full evals weekly, one trace per eval case, CI Langfuse in its own org; ADRs 0002/0004/0005/0007/0009/0010 revised in place; API 1.3.0
- [2026-11-21] v1.1-intent-router-scope: docs: v1.1 scope adds an intent router (ADR-0010) with 6 routes — general chat, smalltalk/help fast path, memory by chat, PDF documents (thread summary, topic news brief), deep research (one plan approval), scheduled tasks (approval, 5 active, daily minimum). PRD 1.2 (US-19…24, FR-060…066, G10, M7, future-intents backlog); functional spec 1.2 (UF-009…012, FS-009…012); architecture 1.2 (`documents`, `schedules` tables); API 1.2.0 (documents/schedules paths, `mode`, `route_selected`/`progress`/`document_created` events); AI design 1.3 (§2.5 subgraphs, `router`/`long_form` roles, new prompts/jobs; GAP-06 partly superseded); eval plan 1.3 (`router.intents`, `documents.golden`, `research.golden`, 13 new metrics, rt-22…26); threat model 1.2 (LLM-T17…T20, kill switches); implementation plan 1.2 (M7: TASK-038…046); baseline targets + `memory/ai.md` updated
- [2026-11-21] design-gap-review: docs: doc 08 v1.2 adds §13 Open Gaps (GAP-01…09: file targeting, whole-doc read, approval ordering for parallel calls, tool-cap unit, platform/other-users scope rule, routing guidance, duplicate memory writes, `forget` resolution, `remember_fact` negative evals); doc 09 v1.2 adds §9 gap coverage, `expected_sequence` routing field, `tools.sequence_accuracy` (advisory) and red-team rt-21
- [2026-11-19] 003-plan-revisions: docs: applied the plan-review decisions (Q1–Q31) — reference build with invite-only hosted instance; goals rewritten (G6/G7 dropped, G8/G9 added); milestones M1–M6 deploy-first (roadmap tasks 004–010); GPT-5/6 model roles with reasoning effort, Responses API `store=false`, `LLM_MODE=fake`; cache-friendly prompt layout; `remember_fact` taint rule; run lease + reaper + drain; Langfuse Cloud EU (self-hosted v4 optional); repo-canonical prompts (ADR-0008); two-tier quality gates (ADR-0009) with restructured eval/coverage baselines; USD budgets; invite codes + admin CLI; data export deferred; stack versions updated (Python 3.14, LangGraph 1.x, Postgres 18, React 19, Tailwind 4, psycopg 3, hand-written SSE client); API spec 1.1.0; ADRs 0002–0007 revised in place
- [2026-11-19] 002-phase1-docs: docs: intake filled (defaults marked), project placeholders replaced, Phase 1 docs 01–10 generated (OpenAPI spec validated), ADRs 0002–0007, memory seeded (project/ai/api/auth), eval baseline seeded with 22 metrics
- [2026-11-18] 001-ai-framework-layer: docs/chore: added AI-engineering layer — intake §11, prompts 08–10 (AI system design, eval plan, LLM threat model), `evals/` gate with `check_baseline.py`, AGENTS §3.8 AI rules + streaming exception, `memory/ai.md`, AI-impact task sections, `make up/down/eval/eval-smoke`, AI env vars

---

## Legend
- `feat:` New feature
- `fix:` Bug fix
- `refactor:` Code change with no functional difference
- `docs:` Documentation only
- `test:` Test additions or changes
- `chore:` Build, config, or tooling changes
