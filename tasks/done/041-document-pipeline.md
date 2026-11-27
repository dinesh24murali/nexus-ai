# Task: 041-document-pipeline

**Status:** done (offline; the real-model items are deferred as D34)
**Created:** 2026-11-22
**ADR refs:** ADR-0010 (router), ADR-0007 (job queue), ADR-0011 (PDF rendering in a killable subprocess; new in this task)

---

## Goal

Turn a conversation, the last answer or a topic into a PDF: the `generate_document` pipeline (gather, compose, persist) plus the `render_document` job that renders it safely.

## Background

Plan v1.3, TASK-041 (M7, `[012-documents-research]`); doc 08 sections 2.5, 2.6, 5, 9; doc 10 LLM-T18; doc 09 (`documents.*`, rt-23). The router and the slash commands already name `generate_document` (`/pdf`, `/pdf last`, `/brief <topic>`); until now the pipeline did not exist and they answered "not available yet". The API (`/documents`, download) and the UI card are TASK-042.

## Acceptance Criteria

- [x] `generate_document` pipeline: `thread`, `last_answer` (no LLM call) and `topic` (one approved `web_search`) sources; `DocumentDraft` on the `long_form` role (max output 4k); persists a `pending` `documents` row and queues `render_document`; emits `document_created`; replies with a short chat message
- [x] `render_document` job: sanitised HTML, deny-all URL fetcher, 60 s limit that really stops the render, <= 30 pages; `failed` documents carry a reason
- [x] Worst-case cost pre-check against the user budget and the global cap, 10 documents/user/day, `cleanup_documents` keeps 100, cancel semantics (doc 08 section 2.3)
- [x] `purge_user` removes `documents` rows and PDF files (the cross-store purge test checks both)
- [x] Fetcher unit test rejects `file://`, `http(s)://` and `data:` outside the allowlist; rt-23 (hostile content) renders with `documents.external_fetches = 0`
- [x] `documents.golden` dataset + step + metrics built and checked offline; `documents.external_fetches = 0` recorded as an absolute gate
- [-] `documents.golden` reviewed, `documents.render_success` and `ops.cost_per_document_run_usd` baselines recorded from a real run — needs a paid run (deferred: D34)
- [-] Real-model run of rt-23 (real compose model on hostile material) — deferred with D34

## Dependencies

- **Tasks:** 038 (done), 040 (UI chip; independent), 023 (web search approval, done)
- **Memory files:** `memory/ai.md`, `memory/api.md`, `backend/AGENTS.md`
- **Docs:** doc 08 sections 2.5, 2.6, 5, 9; doc 09 section 9; doc 10 LLM-T18

## AI Impact

- **Prompts touched:** `nexus.document` v1 (new); `nexus.system` unchanged (its "never offer a PDF" rule is already there)
- **Models / params changed:** `long_form` role is used for the first time (effort `medium`, max output 4000, 120 s)
- **Graph / tools / retrieval changes:** new pipeline `generate_document` (nodes `document_gather`, `document_search`, `document_compose`, `document_persist`); `web_search` approval reused for `topic`; no new model-callable tool
- **Eval datasets affected / added:** `evals/datasets/documents.golden.jsonl` (new)
- **Threat model entries:** LLM-T18, rows for `persist_document` / `render_document` (doc 10 sections 3 and 7)
- **Expected cost / latency delta:** none for chat; a document run costs <= $0.037 worst case (thread/topic), about $0 for `last_answer`

## Files Expected to Change

See `041-document-pipeline.scratch.md`.

## Notes

- The PDF text is untrusted content turned into HTML: raw HTML is escaped, images are not rendered, only `http(s)` links survive and are printed in full.
