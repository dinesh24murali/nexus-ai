# Task: 031-load-resilience-test

**Status:** done
**Created:** 2026-11-22
**ADR refs:** ADR-0005 (fake LLM), ADR-0006 (runs, drain, reaper)

---

## Goal

Show, with a repeatable load run against `LLM_MODE=fake`, that 20 concurrent streams plus 5 requests per second stay inside the NFR latency targets with no errors, that a model failure falls back, and that restarting the API in the middle of the load leaves no conversation locked.

## Background

TASK-031 in `docs/02_outputs/05_implementation_plan.md` (M6); PRD NFR: TTFT < 2 s p95 (no tools), non-streaming API p95 < 300 ms, load-tested at 20 concurrent streams + 5 rps against the fake LLM. No real-model load run (cost). Depends on TASK-023 (done).

## Acceptance Criteria

- [ ] Non-model latency overhead within NFR targets (TTFT p95 < 2 s, non-streaming p95 < 300 ms); no errors under the target load
- [x] No conversation stays locked after an API restart under load (graceful and SIGKILL; for SIGKILL 19 cut-off runs were reaped)
- [x] The fake model can be told to fail (primary only, or both), the real fallback wiring is used in fake mode, and unit tests prove the fallback answers and that a double failure ends the run as an error
- [x] `loadtest/` has a Locust file, a runner that checks the thresholds, and a restart scenario; `make loadtest` runs them against the Compose stack

## Dependencies

- **Tasks:** TASK-023
- **Memory files:** `memory/api.md`, `memory/ai.md`
- **Docs:** doc 01 NFR table, doc 03 section 6, ADR-0006

## AI Impact

- **Prompts touched:** none
- **Models / params changed:** none
- **Graph / tools / retrieval changes:** none (the fake model gains failure markers; the factory builds the fallback chain in fake mode too)
- **Eval datasets affected / added:** none
- **Threat model entries:** LLM-T11 (the "load test" test reference)
- **Expected cost / latency delta:** none (fake model, no spend)

## Files Expected to Change

See `031-load-resilience-test.scratch.md`.

## Notes

- Fake mode is refused in production (`Settings`), so the load run uses the dev Compose stack with `LLM_MODE=fake`.
- Rate limits are per user (30 sends/min), so the run uses a pool of users, like the real invite-only population.

## Outcome

Measured on a dev laptop (Docker, fake model with 0.03 s per word, through Caddy), `make loadtest`, 180 s, 25 users: 3596 requests, 0 failures; `chat ttft` p95 72 ms; `api me` / `conversations` / `usage` / `messages` p95 13 / 18 / 18 / 23 ms; 4.56 sends/s; peak 20 open streams. `make loadtest-restart MODE=graceful` and `MODE=kill` both pass (see CHANGELOG for the numbers). Not measured: a real model, the 4 vCPU VM itself, how many open streams finish before a graceful stop.

Found on the way:
- the **global daily budget ($5) was used up by the load runs** and then every send got `BUDGET_EXCEEDED` (and the clients that kept retrying got `RATE_LIMITED`): the controls work, the overlay lifts both budgets;
- a Locust user that failed mid-run (setup call during the restart) ended the whole run: fixed so only the initial setup is fatal;
- recovery invites must be distinct from the load run's (a used code is refused).
