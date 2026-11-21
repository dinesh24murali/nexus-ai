# Scratch: 007-rag pass 4 (TASK-020 file deletion, TASK-021 files UI)

> Written before any code, as `tasks/AGENTS.md` requires. Task file: `tasks/active/007-rag.md`.
> ADR refs: ADR-0003 (every Qdrant delete/search filters on `user_id`), ADR-0007 (job queue).
> Memory read first: `memory/api.md`, `memory/ai.md`, `memory/project.md` index.

## Plan

### TASK-020 — File deletion (backend)

The spec already has `DELETE /files/{fileId}` (204, 401, 404, 500: "vectors removed before responding"), so **no spec change**.

1. `repositories/files.py`: add `get_for_update` (owner-scoped `SELECT … FOR UPDATE`) and `delete` (owner-scoped).
2. `jobs/queue.py`: add `cancel_queued(session, kind, user_id, payload_key, value)` — mark still-`queued` jobs of that file `cancelled` so a pending ingestion never starts. A `running` job is not touched (see "in-flight" below).
3. `rag/index.py`: add `lazy_index(settings)` (one cached `QdrantIndex` built from settings, like `lazy_retriever`) so `FileService` gets its index without needing a key.
4. `services/files.py`: `FileService.delete(user_id, file_id)`:
   - ONE transaction: lock the row (`FOR UPDATE`; absent or another user's → 404) → `index.ensure_collection()` + `index.delete_file(user_id, file_id)` (tenant-filtered) → cancel queued ingest job → delete the row → commit.
   - AFTER the commit: unlink the disk file (best effort, `missing_ok`, a failure is logged and never fails the request: the row is already gone).
   - If Qdrant fails the transaction rolls back: row, job and disk file stay, the user can retry, and the API returns 500 (documented in the spec). Nothing is half-deleted from the user's point of view.
   - **Order differs from the plan sentence** ("points, then disk file, then row"): the disk file goes last, after the commit, because deleting it before a commit that might still fail would leave a row pointing at nothing.
5. **In-flight ingestion** (FS-004 "deleting a file mid-processing cancels the job and cleans up partial chunks"): the worker's `mark_ready` is an `UPDATE` on the file row, so it blocks on our row lock and then affects 0 rows once the row is gone; `ingest_file` already reacts to that by deleting its points (`index.delete_file`). Points written between our Qdrant delete and our commit are therefore cleaned by the worker. A worker that never reads the row returns early. No new worker code needed; a test proves it (delete between upsert and `mark_ready`).
6. `api/files.py`: `DELETE /{file_id}` → 204; malformed id → 404; log `file_deleted` (ids only).
7. Wiring: `main.py` passes `lazy_index(settings)` into `FileService` (constructor arg with a default built from settings so existing tests keep working).
8. Tests (real Postgres + real Qdrant, all `integration`): see "Tests" below.
9. Spec: no change. `memory/api.md` files section updated (DELETE behaviour). Contract test: schemathesis already fuzzes every spec operation, so `deleteFile` is covered once the route exists (and the "Allow header" exclusion is unrelated); verify it runs.

### TASK-021 — Files UI (frontend)

Generated client only (`api.GET('/files')`, `api.POST('/files')`, `api.DELETE('/files/{fileId}')`); no hand-written fetch.

10. `api/client.ts`: the openapi-fetch adapter does `request.text()`, which would corrupt a binary multipart body. For a `multipart/form-data` request pass `await request.arrayBuffer()` (the boundary header is already on the `Request`); JSON keeps `text()`. Unit test for the adapter.
11. `features/files/api.ts`: `useFileKeys()` (user id in every key), `useFiles()` (one query, `limit=100` ≥ the 50-file cap; `refetchInterval` 2 s while any file is `pending`/`processing`, off otherwise), `useUploadFile()` (mutation via `bodySerializer` building a `FormData`; on success puts the new file in the cache and invalidates), `useDeleteFile()` (mutation; removes from cache on success).
12. `features/files/FilesPage.tsx` (route `/files`, FS "Manage files"): upload button + drop zone (multi-select, **max 5 at once**, extra files are refused with a message), table (name, size, status, chunks, uploaded at, delete), limit indicator `n/50`, empty state, per-row failure reason, delete with an inline confirm (no `window.confirm`), errors through `describeProblem` extended with the file codes (`FILE_TOO_LARGE`, `FILE_TYPE_UNSUPPORTED`, `FILE_DUPLICATE`, `FILE_LIMIT_REACHED`).
13. `features/files/uploads.ts` + `UploadChips.tsx` + `AttachButton.tsx`: the composer's paperclip. `useAttachments()` holds this tab's uploads (name + `uploading` / `error` / file id); chips show **Uploading… → Processing… → Ready / Failed** (UF-004), the post-upload state read from the polled files query; a chip can be dismissed. Wired into `HomePage` and `ChatView` through new optional `Composer` props (`attach` slot); the composer's text behaviour is untouched.
14. `features/chat/Citations.tsx`: chips `[1] q3-report.pdf p.4` under assistant messages that have `citations`; click opens a snippet popover (file name, page, ≤ 300-char excerpt as plain text, Escape / outside click closes, `aria-expanded`). Web citations render only for `http(s)` URLs with `rel="noopener noreferrer"`. Wired into `AssistantBubble`.
15. `AppShell`: a "Files" link in the sidebar; `App.tsx`: route `files`.
16. Frontend tests (Vitest + Testing Library, only `fetch` scripted): files page (list, empty, polling until ready, delete + confirm, duplicate/too-large/limit errors, > 5 files refused, user-id-keyed cache), upload chips in the composer, citation chips and popover (including a hostile snippet/URL), the multipart adapter.
17. `tests/e2e/rag.spec.ts` (fake LLM stack): upload a text file → `Ready` → ask with `[[fixture:rag]]` → answer cites `[1]` with a chip whose popover shows the snippet; plus delete on `/files`. Run against the Compose stack (`make up`, `LLM_MODE=fake`).
18. Gates: `make lint`, `make test`, `make test-integration`, `make verify`; coverage baseline for `frontend/src/features/files` recorded (new package) and `app/services` / `app/jobs` not lowered.
19. Docs: `CHANGELOG.md`, `memory/api.md`, `frontend/AGENTS.md` (project structure line for `features/files`), `memory/project.md` only if it lists status, `tasks/active/007-rag.md` criteria. Then move `007-rag.md` and its scratch files to `tasks/done/` **only if** no criterion is still open for the group — TASK-019/037's `[-]` items are deferred (D1–D9), so the group is done once 020 and 021 are checked.

## Files to create or modify

| File | Action | Notes |
|------|--------|-------|
| `backend/app/db/repositories/files.py` | modify | `get_for_update`, `delete` |
| `backend/app/jobs/queue.py` | modify | `cancel_queued` |
| `backend/app/rag/index.py` | modify | `lazy_index(settings)` |
| `backend/app/services/files.py` | modify | `FileService.delete` + index getter |
| `backend/app/api/files.py` | modify | `DELETE /files/{file_id}` |
| `backend/app/main.py` | modify | pass the index getter |
| `backend/tests/api/test_files_delete.py` | create | HTTP + real Postgres + real Qdrant |
| `backend/tests/services/test_file_delete.py` / `tests/jobs/…` | create | in-flight race, queue cancel |
| `backend/tests/api/test_isolation.py` | modify | user B cannot delete / see A's file (grows with each resource) |
| `frontend/src/api/client.ts` (+ test) | modify | multipart-safe adapter |
| `frontend/src/features/files/{api.ts,FilesPage.tsx,uploads.ts,UploadChips.tsx,AttachButton.tsx,*.test.tsx}` | create | files UI |
| `frontend/src/features/chat/{Citations.tsx,Citations.test.tsx,MessageList.tsx,Composer.tsx,ChatView.tsx}` | create/modify | chips, attach slot |
| `frontend/src/routes/{AppShell.tsx,HomePage.tsx}`, `App.tsx` | modify | link, route, composer wiring |
| `frontend/src/lib/messages.ts` (+ test) | modify | file error texts |
| `tests/e2e/rag.spec.ts` | create | upload → ready → cited answer; delete |
| `tests/coverage-baseline.json` | modify | new gate for `frontend/src/features/files` |
| `CHANGELOG.md`, `memory/api.md`, `frontend/AGENTS.md`, `tasks/active/007-rag.md` | modify | docs and criteria |

## AI impact & eval plan

- Prompts / models / tools changed: **none**. Retrieval is untouched; deletion only removes points through the existing tenant-filtered `QdrantIndex.delete_file`.
- Eval datasets: **none to run** (`make eval` is for prompt/model/graph/retrieval changes; this is neither). `make eval-smoke` is not needed and would cost credits.
- New dataset records: none.
- Tracing: no new spans. Logs carry ids only (`file_deleted`), never names or text.
- Tenant isolation (AGENTS §3.8): the delete must have a test that fails when the `user_id` condition is missing → tested through the HTTP API with two users sharing nothing but a Qdrant collection, mutation-checked once.

## Tests (backend)

- Deleted file: row gone, disk file gone, **no points left**, and `search` (real retriever, real Qdrant) never returns it, while another user's identical-text file is still found (isolation + the TASK-020 criterion).
- Second delete and another user's / unknown / malformed id → the same 404; another user's file and points are untouched.
- Deleting a `pending` file cancels its queued `ingest_file` job; the worker, if it still runs the job, returns without doing anything.
- Deleting mid-processing: delete between the worker's upsert and its `mark_ready` → no points remain afterwards.
- Qdrant failing → 500, and the row, job and disk file are all still there (retry works); a disk-unlink failure after commit still gives 204.
- Re-upload of the same content after deletion works (SHA slot and per-user count are freed).
- Mutation check: drop the `user_id` condition from `delete_file` once and see the isolation test fail.

## Uncertainties

- `request.arrayBuffer()` for multipart through openapi-fetch + `bodySerializer`: verify the browser really sends the boundary header (the e2e test is the proof; jsdom cannot show it).
- Upload **progress**: `fetch` cannot report upload bytes and the rule is "every API call goes through `manager.fetch`" (token refresh must not be bypassed with a raw XHR). The chip therefore shows a busy "Uploading…" state, not a percentage. 20 MB at most, so the state is short. Called out in the task file as a deliberate deviation from the plan's wording "upload with progress".
- Streaming citations: `citation` events arrive during the run, but the live bubble yields to the stored message when the run ends, so chips appear then (the stored message carries `citations`). Showing chips while the text streams is not done (small, and the text would carry `[1]` markers without a chip for a moment).
- Whether the Compose stack builds here without network for images; if `make up` cannot be run, the Playwright spec is written and type-checked but **not run**, and I will say so.

## What will be skipped (and why)

- Byte-level upload progress (above); dragging a file onto the *chat* composer (the paperclip and `/files` dropzone are the spec's two entry points).
- Live citation chips during streaming; `get_file_overview`; file preview/download (no endpoint in the spec).
- Any `Idempotency-Key` handling for delete (idempotent by nature: second call is 404).
- A "files" settings page, memory UI (M5), tool cards (TASK-024).

## Risks

- **Partial deletion**: mitigated by doing Qdrant inside the row-lock transaction and unlinking after commit; worst case is an orphan file on disk (logged), never a searchable orphan.
- **Race with the worker** (upsert after our delete): covered by the row lock plus `mark_ready` returning False; has its own test.
- The multipart adapter change touches every API call: JSON requests keep the exact old path (`text()`), checked by the existing suite.
- Polling every 2 s while files process: stops when none is in progress; the query is user-keyed so nothing leaks across accounts.
- New composer props must not change behaviour for the chat tests (all optional).

## Ready to implement?

Done. Criteria were in `tasks/active/007-rag.md` (TASK-020, TASK-021 sections added with this pass).
