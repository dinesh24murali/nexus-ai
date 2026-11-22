# Scratch: 042-documents-api-ui

> Written before any code (AGENTS.md 3.1). Backend + frontend task. Builds on TASK-041 (table, repository, render job, `Message.documents`).

## Plan

1. **Repository** (`db/repositories/documents.py`): add `list_page` (keyset on `(created_at DESC, id ASC)` like files; the cursor helpers are the shared ones) and `delete` (owner-scoped; returns the `storage_path` it removed, or `None`/not found). `get` exists.
2. **Storage** (`documents/storage.py`): `resolve(uploads_dir, relative) -> Path | None`, the same "must stay under `UPLOADS_DIR`" check `remove` already has, so the download and the delete share one rule.
3. **Service** (`services/documents.py::DocumentsService`): `delete(user_id, document_id)` (row delete in a transaction, then the PDF removed in a thread; the render job already removes a file it wrote for a row that vanished) and `download_path(user_id, document_id) -> (Document, Path)` raising `ApiError` 404 (unknown / not yours / file gone) or 409 `DOCUMENT_NOT_READY` (pending or failed). `main.py` builds ONE `DocumentsService` into `app.state.documents` and hands the same instance to the graph factory; `deps.py` gains `DocumentsDep`.
4. **API** (`api/documents.py`, thin): `GET /documents`, `GET /documents/{id}`, `DELETE /documents/{id}` (204), `GET /documents/{id}/download` (`FileResponse`, `application/pdf`, `Content-Disposition: attachment` with an ASCII `filename` built from the title and `filename*`, `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`). A malformed id is the same 404 as an unknown one. `DocumentPage` schema added to `api/schemas.py`. Router registered in `main.py`.
5. **Spec**: already complete; checked for drift by the contract tests. Add `DocumentOut` fields check only (no change expected). If the spec needs a note (download headers) it is a description change, patch version bump.
6. **Backend tests:** `tests/api/test_documents.py` (list order + pagination + bad cursor 400 + limit 422, get, delete removes row and file, download bytes/headers/filename sanitising, 409 pending and failed, 404 file missing, malformed id), isolation cases in `tests/api/test_isolation.py` (B gets 404 on A's get/delete/download/list empty, A's file survives), `tests/services/test_documents_service.py` additions (delete, download_path), `storage.resolve` unit test (traversal refused). Contract: add the four paths to the include regex and, where the cursor is opaque, `GET /documents` to the positive-data exclusion (like `/memories`).
7. **Frontend generated types:** `npm run gen:api` (no spec change expected -> no diff; `components['schemas']['DocumentObject']` is already generated).
8. **Frontend `features/documents/`:** `api.ts` (`useDocumentKeys`, `useDocuments` (poll every 2 s only while one is `pending`), `useDocument(initial)` (card: seeded from the message, polls while pending, a 404 = deleted), `useDeleteDocument`, `useDownloadDocument` (`manager.fetch` -> blob -> object URL -> anchor `download` -> revoke; the file name comes from the title)), `DocumentCard.tsx` (text only, one `role="status"` only while preparing... decided in the card tests: pages keep exactly one `status` rule from frontend/AGENTS.md, so the card uses `aria-live` text, not `role="status"`), `DocumentsPage.tsx`, `messages.ts` (kind labels, size formatting reuse, problem text).
9. **Chat wiring:** `useChatRun` keeps `documents` from `document_created` (live), `MessageList` renders `DocumentCard`s under stored answers (`message.documents`) and live ones in the live bubble; they yield to the stored message at the same moment as the text (same id -> same query key, so the card does not flicker). `api/stream.ts` already knows the event.
10. **Routing/nav:** route `documents` in `App.tsx`, "Documents" link in `AppShell`.
11. **Frontend tests:** `DocumentCard.test.tsx` (pending -> ready by polling, failed with reason, deleted, download calls the API with the token and saves a blob, title rendered as text), `documents.test.tsx` (page: list, empty, delete confirm/cancel/error, nav link), `chat.test.tsx` addition (live card from `document_created`, stored card, no duplicate), `MessageList` test.
12. **E2E** `tests/e2e/documents.spec.ts` (fake LLM on api AND worker): `/pdf` -> card shows Preparing/Ready -> Download saves a real PDF (`%PDF`) -> page lists it -> delete -> gone; user B's session cannot fetch A's document. Update `commands.spec.ts` header note (card now exists).
13. **Docs/housekeeping:** doc 10 (LLM-T18 download endpoint built, tests named), `memory/api.md` (documents API), `memory/ai.md` only if a fact changes, `frontend/AGENTS.md` (Documents section, remove "not built yet" TASK-042 note), `backend/AGENTS.md` structure line (`api/documents.py`), `tests/coverage-baseline.json` if a package gate must ratchet, `tasks/deferred.md` (D33 done, remove), CHANGELOG, plan checkboxes, move task files to `done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/app/db/repositories/documents.py` | modify | `list_page`, `delete` |
| `backend/app/documents/storage.py` | modify | `resolve` |
| `backend/app/services/documents.py` | modify | `delete`, `download_path` |
| `backend/app/api/documents.py` | create | four routes |
| `backend/app/api/schemas.py`, `deps.py`, `main.py` | modify | `DocumentPage`, `DocumentsDep`, one shared service, router |
| `backend/tests/api/test_documents.py`, `test_isolation.py`, `tests/services/test_documents_service.py`, `tests/documents/test_storage.py`, `tests/contract/test_api_contract.py` | create / modify | |
| `frontend/src/features/documents/{api.ts,DocumentCard.tsx,DocumentsPage.tsx,messages.ts}` + tests | create | |
| `frontend/src/features/chat/{useChatRun.ts,MessageList.tsx}`, `routes/AppShell.tsx`, `App.tsx` | modify | live + stored cards, nav, route |
| `tests/e2e/documents.spec.ts`, `commands.spec.ts` | create / modify | |
| docs, memory, AGENTS, CHANGELOG, plan, deferred | modify | |

## AI Impact & Eval Plan

none. No prompt, model, graph, tool or retrieval change; `make eval` is not needed. Tracing: no new spans; the endpoints log ids only (never titles or contents).

## Uncertainties

- **Blob download under the CSP** (`default-src 'self'`): a `blob:` object URL clicked through an anchor with `download` is a navigation/download, not a fetch, so it should not need `blob:` in the policy. The e2e test proves it in a real browser; if it fails, the fix is in the Caddyfile policy and is noted here.
- **A `ready` row whose file is gone** (disk wiped, restore without uploads): 404 (the document is not downloadable), logged with the id. Not 409, since waiting does not help.
- **The card inside `aria-live` log**: the message list is `role="log"` `aria-live="polite"`; status text changes inside it are announced once, which is wanted.
- **Poll cost**: one `GET /documents/{id}` per pending card every 2 s, stops at `ready`/`failed`; a render is <= 60 s.

## What Will Be Skipped

- Rename, bulk delete, search/filter, preview of the PDF in the page (not in the plan).
- A signed-URL download (the plan says Bearer fetch -> blob; no token in a URL).
- Deleting a document when its conversation is deleted (the spec says a document outlives its chat; the FK sets `conversation_id` to null).
- Paid real-model run: nothing here needs one.

## Risks

- **IDOR**: every query takes `current.id`; a test for each operation (list, get, delete, download) with a second user; the path read from the DB is resolved under `UPLOADS_DIR` before it is opened.
- **Header injection through the title** in `Content-Disposition`: the title is reduced to a safe ASCII set for `filename`, percent-encoded for `filename*`; tested with quotes, CR/LF, `;`, unicode and an empty title.
- **Delete during render**: the delete removes the row first; the job's `mark_ready` then returns False and the job removes the file it wrote (existing, tested in TASK-041).
- **One `DocumentsService` instance** now serves the graph and the API: it holds only the session factory and settings, so sharing is safe.

## Ready to implement?

Yes.
