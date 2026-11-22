# Task: 030-security-review

**Status:** done
**Created:** 2026-11-22
**ADR refs:** ADR-0009 (gates), ADR-0004 (redaction)

---

## Goal

Walk the doc 10 threat table against the code, check the deployment headers, upload limits, invite handling, dependencies, trace redaction and secrets handling, fix what is wrong, and make "every threat has a mitigation and a test" a fact a test checks.

## Background

TASK-030 in `docs/02_outputs/05_implementation_plan.md` (M6). Depends on TASK-028 (done). Files named by the plan: `deploy/Caddyfile`, `backend/app/core/*`. The review is a read of the code, not a rewrite: each finding is fixed, or recorded in `tasks/deferred.md` with the reason.

## Acceptance Criteria

- [x] Every active LLM-Txx threat (T01-T16) has a mitigation and a named test reference in doc 10; T17, T18 and T20 (M7 features, not built) are listed with the M7 task that owns their test
- [x] A test fails if a threat id has no reference, or a reference points at a file or test that does not exist
- [x] CSP and security headers reviewed against the Caddyfile and fixed; a test pins them (`Cache-Control: no-store` on the API was tried and removed, D23)
- [x] Upload limits (Caddy, API, parsers), invite handling, token and cookie handling, trace redaction and secrets handling each reviewed, with the result written in the review notes
- [x] Dependency audit run (`pip-audit`, `npm audit`): no known vulnerabilities; `make audit` repeats it
- [x] Findings that cannot be fixed now are in `tasks/deferred.md`

## Dependencies

- **Tasks:** TASK-028
- **Memory files:** `memory/auth.md`, `memory/api.md`, `memory/ai.md`
- **Docs:** doc 10 (threat model), doc 03 section 6 (infrastructure), `evals/AGENTS.md`

## AI Impact

- **Prompts touched:** none
- **Models / params changed:** none
- **Graph / tools / retrieval changes:** none
- **Eval datasets affected / added:** none
- **Threat model entries:** all of T01-T16 get a test reference; T17, T18, T20 stay with M7
- **Expected cost / latency delta:** none

## Files Expected to Change

See `030-security-review.scratch.md`.

## Notes

- The review of a threat the code does not yet implement is not a pass: the 100-web-searches-per-day limit (D13) and the red-team measurements (D19) stay open and are named in the review notes.

## Outcome

Review result table: doc 10, section "Security review (TASK-030)". Fixed: CSP gaps, missing browser-feature headers, unhardened api/worker containers, no worker memory limit, web links that hid their destination. Corrected doc claims that were not true (T02, T03, T09, T12). New tests read the deploy files, the log calls and the threat table. Not fixed, recorded: D21 (no CI for audits, prompt canary), D22 (worker egress), D23 (`no-store` hangs the DOM check), and the older D13 and D19. `make verify`: 34 of 35 e2e tests pass; the failing one (`memory.spec.ts` edit) fails the same on the code before this task (D24); DOM checks 6 of 6.
