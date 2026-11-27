# Task: 028-account-deletion-purge

**Status:** done
**Created:** 2026-11-22
**ADR refs:** ADR-0007 (job queue), ADR-0009 (coverage gates)

---

## Goal

A user can delete their account: `DELETE /me` revokes every session at once and schedules a purge that erases their data from Postgres, the LangGraph checkpoints, the memory store, Qdrant and the uploads disk.

## Background

TASK-028 in `docs/02_outputs/05_implementation_plan.md` (M6); PRD FR-004 (erased within 24 h), functional spec UF-008 / section 8 (retention) and the `users.status` state machine (`active -> pending_deletion -> gone`). The API contract (`DELETE /me`, `DeleteAccountRequest`) is already in `docs/02_outputs/04_api_spec.yaml`. Data export is deferred past v1.

## Acceptance Criteria

- [x] Cross-store purge test: after the job, zero Postgres rows, checkpoints, store (memory) items, Qdrant points and files remain for the user, and another user's data in every store is untouched
- [x] `DELETE /me` with the right `confirm_email` returns 202, revokes every session (refresh families) and sets `pending_deletion`; a wrong email is refused and changes nothing
- [x] A user waiting for deletion cannot sign in or use an access token; their queued jobs are cancelled
- [x] The purge job is idempotent (a second run, or a run after a crash half way, finishes the work) and registered in `jobs/handlers.py::JOBS`
- [x] Contract test covers `DELETE /me`; coverage gates hold (jobs 97.68, services 94.51; `app/llm` 96.92 vs recorded 97 is the older discrepancy, untouched by this task)

## Dependencies

- **Tasks:** TASK-020 (file delete), TASK-025 (memory store)
- **Memory files:** `memory/api.md`, `memory/auth.md`, `memory/ai.md`
- **Docs:** functional spec UF-008 and section 8, PRD FR-004, tech architecture (worker jobs)

## AI Impact

- **Prompts touched:** none
- **Models / params changed:** none
- **Graph / tools / retrieval changes:** none (the purge removes retrieval and memory data; no model call)
- **Eval datasets affected / added:** none
- **Threat model entries:** none new (no new tool)
- **Expected cost / latency delta:** none

## Files Expected to Change

See `028-account-deletion-purge.scratch.md`.

## Notes

- Memory text is user data: never logged. Logs carry the user id and counts only.

## Outcome

Backend 1594 tests pass (real Postgres and Qdrant). Not built: the Settings -> Account UI (`tasks/deferred.md` D18). While closing the task, the contract test's note that the Allow-header exclusion would go away with `DELETE /me` turned out to be wrong (Starlette lists only the first route's methods on a 405 when a path has one route per method); the exclusion stays with an accurate comment.
