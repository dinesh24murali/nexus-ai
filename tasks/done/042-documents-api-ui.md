# Task: 042-documents-api-ui

**Status:** done
**Created:** 2026-11-22
**ADR refs:** ADR-0011 (PDFs are made by a killable child process; this task only serves the finished file), ADR-0006 (runs are server-side; the card reads the document, not the stream)

---

## Goal

Let a user list, open, download and delete the PDFs the `generate_document` pipeline makes: the `/documents` API, the `DocumentCard` under the answer that made one, and the `/documents` page.

## Background

Plan v1.3, TASK-042 (M7, `[012-documents-research]`); `docs/02_outputs/04_api_spec.yaml` already has `listDocuments`, `getDocument`, `deleteDocument`, `downloadDocument` and `DocumentObject` (spec 1.4.x, written for TASK-041); doc 10 LLM-T18 says the download endpoint (`Content-Disposition: attachment`, owner-scoped 404) comes with this task. `tasks/deferred.md` D33 (the card) is closed by it. The pipeline, the table, the render job and `Message.documents` exist (TASK-041).

## Acceptance Criteria

- [x] `GET /documents` (keyset pages, newest first), `GET /documents/{id}`, `DELETE /documents/{id}` (row and PDF), `GET /documents/{id}/download` (PDF, `Content-Disposition: attachment`; 409 `DOCUMENT_NOT_READY` while pending or failed)
- [x] Every query is owner-scoped; another user's document, an unknown id and a malformed id are the same 404 (list, get, delete, download)
- [x] Contract tests cover the four operations (schemathesis, the exclusion list in `tests/contract/test_api_contract.py` grows); IDOR test: user B gets 404 on user A's document and on its download, and A's PDF survives B's delete attempt
- [x] Deleting a document removes its file; a delete during a render leaves no file (the job's existing check); deleting a conversation keeps the document (FK SET NULL, already built)
- [x] `DocumentCard` under the answer (live from `document_created`, stored from `Message.documents`): Preparing... (polls) -> Ready (pages, size, Download) or Failed (the reason); a deleted document says so; the download is a Bearer fetch -> blob -> save
- [x] `/documents` page (list, status, Download, inline-confirm delete, link to the chat) and a sidebar link; component tests and a Playwright spec (`tests/e2e/documents.spec.ts`, fake LLM) cover the happy path, delete and the empty state
- [x] `make verify` passes (except the known D24 flake of `memory.spec.ts`, which passes alone); coverage gates not lowered; `frontend/src/features/documents` gate 96 recorded

## Dependencies

- **Tasks:** 041 (done)
- **Memory files:** `memory/api.md`, `memory/ai.md` (documents), `backend/AGENTS.md`, `frontend/AGENTS.md`
- **Docs:** `docs/02_outputs/04_api_spec.yaml`, doc 10 LLM-T18, doc 02 (functional spec, documents)

## AI Impact

none (no prompt, model, graph, tool or retrieval change). The download serves bytes the renderer already produced; nothing here calls a model.

## Files Expected to Change

See `042-documents-api-ui.scratch.md`.

## Notes

- A document title is model-written text: it is sent only as JSON text (React escapes it) and, in the download header, as a sanitised ASCII `filename` plus an RFC 5987 `filename*`.
