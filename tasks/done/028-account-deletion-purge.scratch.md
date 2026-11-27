# Scratch: 028-account-deletion-purge

## Plan

1. **Service** `services/accounts.py::request_deletion(factory, user, family_id?)`: in ONE transaction lock the user row, check status, set `pending_deletion`, revoke all refresh tokens, cancel the user's `queued` jobs (any kind except `purge_user`), enqueue `purge_user` (idempotency key `purge:<user_id>`, so a double click enqueues once). Returns nothing; the API answers 202.
2. **Endpoint** `DELETE /me` in `api/me.py`: body `DeleteAccountRequest{confirm_email}`; mismatch (case-insensitive, the column is citext) -> `ApiError(400, "CONFIRMATION_MISMATCH")`, nothing changes. 202 with empty body. Add the code to the spec's description (400 is already in the spec).
3. **Job** `purge_user` in `jobs/handlers.py` (timeout 600 s, 5 attempts), steps in this order, each idempotent:
   a. Qdrant: delete every point with payload `user_id` (new `QdrantIndex.delete_user`).
   b. Memory store: `MemoryStore.clear(user_id)` (all memories of the namespace).
   c. Disk: remove `uploads_dir/<user_id>/` (`shutil.rmtree`, ignore missing).
   d. Postgres, ONE transaction: delete LangGraph checkpoint rows for every conversation of the user (new repo function `conversations.delete_all_checkpoints`), delete rate-limit rows keyed `chat:<user_id>`, delete the user's remaining jobs except this one, delete the `users` row (FK cascade: refresh tokens, conversations, messages, files, feedback, usage_daily; `invites.used_by` and `jobs.user_id` become NULL).
   The user row goes last: a crash earlier leaves `pending_deletion`, and the retry redoes everything. After the row is gone `job.user_id` is NULL (FK SET NULL), so the handler also reads `payload.user_id` and treats "no such user" as "already purged" after re-running the external-store steps.
4. **Safety nets** for work in flight: `extract_memories` already skips users that are not `active`; check `ingest_file`/`generate_title` do the same-or-no-op for a missing row (tests), and that a run ending after the purge writes nothing (lease check, conversation gone).
5. **Tests** (TDD): service unit/integration (wrong email, 202, tokens revoked, login refused, job enqueued once, queued jobs cancelled), API tests, `tests/jobs/test_purge_user.py` cross-store test with two users (Postgres + checkpoints + store + Qdrant + disk, the second user untouched), idempotency and crash-resume tests, contract test.
6. Docs: API spec note, `memory/api.md`, `memory/auth.md`, `memory/ai.md` (retrieval/memory purge), `backend/AGENTS.md` conventions, `CHANGELOG.md`, `tests/coverage-baseline.json`, move task + scratch to `tasks/done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/app/services/accounts.py` | create | `request_deletion` |
| `backend/app/api/me.py`, `api/schemas.py` | modify | `DELETE /me`, `DeleteAccountRequest` |
| `backend/app/jobs/handlers.py` | modify | `purge_user` + registry |
| `backend/app/rag/index.py` | modify | `delete_user` (filter by `user_id`) |
| `backend/app/db/repositories/conversations.py`, `jobs`/`rate_limits` repos | modify | bulk checkpoint delete, job and rate-limit cleanup |
| `backend/tests/**` (api, jobs, services, contract) | create/modify | see plan step 5 |
| `docs/02_outputs/04_api_spec.yaml` | modify | document 400 `CONFIRMATION_MISMATCH` and the purge timing |
| `memory/*.md`, `backend/AGENTS.md`, `CHANGELOG.md`, `tests/coverage-baseline.json` | modify | |

## AI Impact & Eval Plan

none (no prompt, model, graph, retrieval or memory behaviour change; the purge only removes data). Tracing: no new spans; logs carry ids and counts only.

## Uncertainties

- Whether the LangGraph `store` table is reached by `MemoryStore.clear` for users with more than one page of items (check `all`).
- Qdrant filter on `user_id` needs the payload index; `delete_file` already filters on it, so it exists (verify).
- Which jobs of a user can be `running` at the same moment (ingest, extract): they are left to finish; their handlers must no-op on a missing user/file.

## What Will Be Skipped

- **The Settings -> Account page (UF-008 frontend).** `/settings/account` does not exist yet (no display name, password or delete UI), and the plan lists only backend files for this task. Recorded in `tasks/deferred.md`; the endpoint is complete and tested without it.
- Langfuse trace deletion: traces carry only a hashed user id and expire on their own (functional spec section 8).
- Data export (deferred past v1 by the plan).

## Risks

- A purge that deletes the user row before the external stores would strand vectors/memories: hence the order (user row last) and the idempotent retry.
- Qdrant or Postgres unavailable: the job fails, backs off and retries (5 attempts), and the user stays `pending_deletion` (cannot sign in), never half-visible.
- Blast radius of a bug in the bulk deletes is every user: every statement is filtered by the one user id, and the test checks a second user is untouched in each store.

## Ready to implement?

Yes.
