# Scratch: 031-load-resilience-test

## Plan

1. **Fault injection in the fake model.** `[[fail:primary]]` / `[[fail:all]]` in the newest human message makes the scripted model raise a connection error. In fake mode `get_model` builds the same primary + fallback chain as real mode (two `FakeChatModel`s; the primary fails on both markers, the fallback only on `fail:all`), so the real `with_fallbacks` wiring and `_FALLBACK_ON` list are exercised. Unit tests: normal reply, primary down -> fallback answers, both down -> the run ends in an error event and the conversation is not left locked.
2. **Load scenario** `loadtest/locustfile.py` (Locust, in its own small `loadtest/pyproject.toml` so the app image does not carry it):
   - a pool of users created from invite codes (the CLI makes the codes; the runner passes them in), each login once
   - `StreamUser` x 20: send a message, read the SSE stream, record TTFT (time to the first `token` event) and total time as Locust custom events, loop with a short think time
   - `ApiUser`: non-streaming reads (`/me`, `/conversations`, `/me/usage`, message list) at a constant 5 requests per second overall
3. **Runner** `loadtest/run.sh` + `loadtest/check.py`: start the stack with `LLM_MODE=fake` and a realistic token delay, create invites, run Locust headless for a fixed time, then `check.py` reads Locust's CSV and fails if there are failures, the non-streaming p95 is over 300 ms, or the TTFT p95 is over 2 s.
4. **Restart scenario** `loadtest/restart.sh` + `loadtest/check_locks.py`: the same load, `docker compose restart api` in the middle, then after the reaper window poll Postgres until no conversation has `active_run_id` set; fail if any stays locked.
5. `make loadtest` target; document in `README`/`memory/infra`-style note (`memory/api.md` run facts) and the deferred file if something cannot be done.
6. Run it for real against the Compose stack and record the measured numbers in the task file.

## Files to change (estimate)

- `backend/app/llm/fake.py` (failure markers, model name), `backend/app/llm/factory.py` (fake fallback chain)
- `backend/tests/llm/test_fake_failures.py` (new), maybe `backend/tests/graph/` for the run-level check
- `loadtest/locustfile.py`, `loadtest/pyproject.toml`, `loadtest/run.sh`, `loadtest/restart.sh`, `loadtest/check.py`, `loadtest/check_locks.py`, `loadtest/README.md` (new)
- `Makefile` (`loadtest`, `loadtest-restart`), `.gitignore` (`loadtest/results/`)
- `tests/coverage-baseline.json` only if a package is added (not expected)
- `memory/ai.md` (fake failure markers), `CHANGELOG.md`, `docs/02_outputs/10_llm_threat_model.md` (LLM-T11 "load test" reference)

## Uncertainties

- Whether the stack builds and runs in this environment (Docker is up, Postgres and Qdrant already run). The api/worker/caddy images are built by `make up`.
- Locust installs from PyPI (network needed). If unavailable, I fall back to a small asyncio/httpx load generator and say so.
- Fake-mode cost: user daily budget is $0.50; the fake model's pricing must not trip `BUDGET_EXCEEDED` mid-run. If it does, the runner sets a higher budget through env for the run only.
- The per-user limit is 30 sends/min, so 20 streams need a pool of users, not one account.
- How the unauthenticated flow limits (login/register rate limits) interact with creating a pool at start.

## What will be skipped, and why

- No real-model load run (the plan says so; it costs money).
- No Prometheus metrics (deferred by the plan).
- No horizontal scale test (one VM, one API worker).

## Risks

- Timings on a dev laptop are noisier than on the 4 vCPU VM; thresholds are the NFR ones, and the run records the numbers rather than tuning gates to pass.
- Restarting the API mid-run drops the streams of the 20 users; the check is about locks and reaping, not about those streams succeeding. Drain is 90 s and the reaper stale window is 60 s, so the lock check polls for a few minutes.

## What happened (added at the end)

Everything in the plan was built. Locust installed fine (own `loadtest/pyproject.toml`). Deviations: peak open streams (not a mean) is the concurrency check; the lock check compares the reaped count with a baseline taken before the restart; the overlay lifts the budgets.
