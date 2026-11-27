# Scratch: 030-security-review

## Plan

1. Review, one area at a time, reading the code (not trusting doc 10) and recording a result per area in "Review notes" below:
   a. Caddy headers and CSP, API cache headers, body limits
   b. Containers: user, capabilities, limits, published ports, volumes
   c. Auth: invite codes, password and token handling, cookie attributes, enumeration, rate limits
   d. Uploads: size limits at Caddy and API, parser limits (decompressed size, pages), file names and storage path
   e. Trace and log redaction, hashed user id, no content in logs
   f. Secrets: settings, `.env.example`, `.gitignore`, production refusals, image contents
   g. Dependencies: `pip-audit` (via uvx) and `npm audit`
2. Threat traceability: a table in doc 10 mapping T01-T16 to concrete test files/names (found by searching the tests, not assumed) and T17/T18/T20 to the M7 tasks; `backend/tests/core/test_threat_model.py` parses it and fails on a missing threat id, a missing path or a test name not in the file.
3. Fix findings. Each fix gets a test where a test can see it (headers: parse the Caddyfile; containers: parse compose.yml).
4. Anything not fixed goes in `tasks/deferred.md` with the condition that unblocks it.
5. Update doc 10 (status section), `memory/api.md` or `memory/auth.md` where facts change, `CHANGELOG.md`; move the task and scratch to `tasks/done/`.

## Files to change (estimate)

- `deploy/Caddyfile` (CSP extras, Permissions-Policy, COOP/CORP, `Cache-Control: no-store` on `/api/*` unless the handler set one)
- `compose.yml` (api/worker: `cap_drop`, `no-new-privileges`, worker memory and pids limits)
- `docs/02_outputs/10_llm_threat_model.md` (traceability table, review result, honest status of T12 egress)
- `backend/tests/core/test_threat_model.py` (new), `backend/tests/core/test_deploy_config.py` (new)
- `tasks/deferred.md`, `CHANGELOG.md`, `memory/*.md`
- possibly `backend/app/*` for findings from the review (unknown until read)

## Uncertainties

- Whether dependency audits can reach the network here; if not, the result is recorded as "not run" and deferred, not as a pass.
- Caddy cannot be started inside the unit tests; I validate the Caddyfile with the `caddy:2.11` image if Docker allows (`caddy validate`), and pin the header text with a plain parse.
- T12 says "no network egress except OpenAI/Qdrant/Postgres". Plain Compose cannot enforce an egress allow-list. Expect to record it as an accepted gap, not to build a proxy.

## What will be skipped, and why

- The 100 web searches/user/day counter (D13), the first real-model red-team measurement (D19): need product work or paid runs; named in the notes.
- Penetration-style testing of the live stack beyond what the existing suites do.
- M7 threats (T17, T18, T20): features not built.

## Risks

- A stricter CSP could break the SPA (inline styles, fonts, Shiki). The e2e suite and `make verify` need to run against the stack after the change.
- `cap_drop: ALL` on api/worker could break something (uvicorn binds 8000, so it should be fine); test by bringing the stack up.

## Review notes

See the result table in doc 10 ("Security review (TASK-030)"); that is the record. Differences from the plan above: `Cache-Control: no-store` was added then removed (D23); the Citations component got a change (host shown) because the review found T02's "domain shown" claim untrue; `make audit` was added.
