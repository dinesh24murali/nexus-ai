# Scratch: 041-document-pipeline

> Written before any code (AGENTS.md 3.1). Backend task. Builds on TASK-038 (router, dispatch) and TASK-023 (web search approval).

## Plan

1. **Migration `0004_documents`** + model `Document` (`documents`: id, user_id FK cascade, conversation_id FK SET NULL, message_id (plain uuid: the assistant message is written after the document), title, kind CHECK(thread_summary|answer|news_brief|research_report), status CHECK(pending|ready|failed), draft JSONB, storage_path, page_count, size_bytes, error, created_at, updated_at; index (user_id, created_at DESC, id)). Repository `db/repositories/documents.py`: every function owner-scoped (create under the user row lock with the daily limit, get, count_today, for_messages, mark_ready/mark_failed, delete_older_than_keep).
2. **`app/documents/draft.py`:** `DocumentDraft` (title, sections[{heading, body_md}], sources[{index, title, url}]) with the limits (title 120, <= 12 sections, body 6000, <= 20 sources); `draft_from_answer(text, title_hint, citations)` (no LLM: one section per `#` heading, sources from the answer's citations); `Material` builders for `thread` (summary + newest messages <= 16k tokens, wrapped as data) and `topic` (web results as `<web_result>`-style data).
3. **`app/documents/render.py`:** Markdown -> HTML with `markdown-it-py` (raw HTML disabled, `image` rule disabled), then an `nh3` allowlist (links only `http`/`https`, printed in full after the text); a fixed stylesheet bundled in the package; `render_pdf(html, out, timeout_s, max_pages)` runs WeasyPrint in a **subprocess** (`python -m app.documents.worker_main`: HTML on stdin, PDF to a path, JSON `{pages, fetches}` on stdout) that is killed at the timeout (ADR-0011: a thread cannot be killed). **`app/documents/fetcher.py`:** the url_fetcher: only `nexus-bundled:` names from a fixed table are served; everything else (`file:`, `http(s):`, `data:`, `ftp:`...) raises and is counted.
4. **`app/graph/intents/generate_document.py`:** nodes `document_gather` (source -> material; none available -> a chat reply), `document_search` (topic only: `interrupt()` with a `web_search` card, runs the approved/edited query once; reject -> reply, no document), `document_compose` (`thread`/`topic`: `nexus.document` + `long_form` strict structured output; `last_answer`: no LLM), `document_persist` (writes the row, queues `render_document`, emits `document_created`). First node `document_check` is the pre-check (daily limit, worst-case cost vs the user's remaining budget and the global cap): refusing = a chat message, nothing runs. A `DocumentService` protocol is injected (reads for gather, the limit check, persist); production = `services/documents.py`.
5. **Router/builder/runs:** `PIPELINES` gains `generate_document`; `build_graph(documents=...)` adds the nodes and the dispatch branch; `RunService` turns the custom event `document_created` into the SSE event, passes `assistant_message_id` in the run config, adds `nexus.document` to the traced prompts; `graph_factory_for(document_service=)`; `main.py` wires it. `/pdf`, `/pdf last`, `/brief` stop answering "not available yet". `Message.documents` is filled from the table (`MessageOut`).
6. **`nexus.document` prompt v1**, `sse.py::DocumentCreatedEvent` (spec already has it), fake model: `long_form` role returns a scripted `DocumentDraft` JSON built from the material (and `[[doc:invalid]]` for a bad output).
7. **Jobs:** `render_document` (60 s, 2 attempts, key `render:{id}`; owner-scoped; idempotent; writes `<uploads_dir>/<user_id>/documents/<id>.pdf`; failure reasons are fixed texts, never exception text; a document deleted meanwhile leaves no file) and periodic `cleanup_documents` (daily, keep the newest 100 per user, deletes rows then files). `purge_user` already removes the user directory; the test now proves rows and the PDF are gone.
8. **Dockerfile:** WeasyPrint system libs (`libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core`) in the runtime stage; `weasyprint`, `markdown-it-py`, `nh3` in `pyproject.toml` (lock updated).
9. **Evals:** `documents.golden.jsonl` (12 records, 4 smoke: thread/last_answer/topic cases with `key_facts`, three hostile records = rt-23 with `threat_id`), `DocumentCase` in `datasets.py`, step `evals/documents.py` (real graph pipeline + real renderer; metrics `documents.render_success`, `documents.external_fetches`, `documents.fact_coverage` (the existing facts judge), `ops.cost_per_document_run_usd`), wired into `evals/run.py` (`--no-documents`), `eval-baseline.json` gets the absolute gate `documents.external_fetches = 0` and targets for the rest.
10. **Docs:** ADR-0011, doc 10 (rows + LLM-T18 built), doc 09 status, `memory/ai.md`, `memory/api.md`, `backend/AGENTS.md`, `tests/coverage-baseline.json` if a package is new (`app/documents`), CHANGELOG, plan checkboxes, `tasks/deferred.md` (D33 card, D34 paid run), move task files to `done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/alembic/versions/0004_documents.py`, `backend/app/db/models.py` | create / modify | table + model; drift test must stay green |
| `backend/app/db/repositories/documents.py` | create | owner-scoped queries |
| `backend/app/documents/{__init__,draft,render,fetcher,worker_main}.py`, `assets/document.css` | create | pure parts + subprocess renderer |
| `backend/app/graph/intents/generate_document.py`, `intents/__init__.py` | create / modify | pipeline, `PIPELINES` |
| `backend/app/graph/builder.py`, `state.py` | modify | nodes, dispatch, state keys (`document_material`, `document_kind`) |
| `backend/app/services/documents.py` | create | production `DocumentService`, limit and budget check |
| `backend/app/services/runs.py`, `app/api/sse.py`, `app/api/schemas.py`, `app/main.py`, `app/api/chat.py` | modify | event, wiring, `Message.documents` |
| `backend/app/graph/commands.py` | modify | nothing if `PIPELINES` drives availability; check `/help` text |
| `backend/app/jobs/handlers.py`, `periodic.py` | modify | `render_document`, `cleanup_documents` |
| `backend/app/llm/fake.py` | modify | scripted `long_form` |
| `backend/app/core/config.py` | modify | `documents_per_day` (10), `documents_keep` (100) |
| `backend/prompts/nexus.document.md` | create | v1 |
| `backend/Dockerfile`, `backend/pyproject.toml`, `backend/uv.lock` | modify | libs + dependencies |
| `backend/evals/{documents.py,datasets.py,run.py}`, `evals/datasets/documents.golden.jsonl`, `evals/eval-baseline.json` | create / modify | suite |
| `backend/tests/...` | create | draft, render, fetcher, pipeline (graph), service, job, periodic, purge, API `Message.documents`, evals step |
| `adr/0011-pdf-rendering-subprocess.md` | create | |
| docs, memory, CHANGELOG, plan, deferred | modify | |

## AI Impact & Eval Plan

- Prompts / models: `nexus.document` v1 (new), `long_form` role first used. Unit tests use the fake LLM.
- Evals: `documents.golden` (12 / 4 smoke) built and run offline in `LLM_MODE=fake` (pipeline check only). **No paid run** (same reason as D10/D15/D17/D19/D29): the review of the records, the first real numbers and the `documents.render_success` / `ops.cost_per_document_run_usd` baselines are deferred (D34). `documents.external_fetches = 0` is deterministic (the renderer needs no model for hostile material) so it is recorded as an absolute gate now, like `tools.approval_enforced`.
- New dataset records: 12 (3 hostile = rt-23, the rest `thread`, `last_answer`, `topic`).
- Tracing: `nexus.document` in the run trace's prompt versions; tag `feature:documents` is not added (Langfuse tags are fixed at run start; the intent tag covers it); `job.render_document` traced by name only; no document text in any trace or log (ids, counts and reason codes).

## Uncertainties

- WeasyPrint subprocess start-up time (~1 s): acceptable inside a 60 s job. Pango/fonts exist on this machine, so the renderer runs here and in the rebuilt image.
- `thread` gather reads the `messages` read model (doc 08) through the service, plus the checkpoint `summary` from graph state.
- Whether a router-chosen `generate_document` needs the budget check at the API (it cannot: the intent is only known in the graph) -> the check is the first node of the pipeline.
- `markdown-it-py` + `nh3` vs the client's allowlist: same intent (no raw HTML, no images, only http(s) links), not byte-identical.

## What Will Be Skipped

- `/documents` endpoints, the card, polling and the page (TASK-042). `Message.documents` is filled now because the table exists and a reload would otherwise lose which message made a document.
- `research_report` kind is in the CHECK constraint (the spec has it) but only TASK-043 writes it.
- Paid real-model run, review of the records, baselines (D34).
- The 100-per-day Tavily counter (D13) is unchanged; one approved search per `/brief`.
- `documents.faithfulness` (judge, advisory): not built; `documents.fact_coverage` reuses the existing facts judge.

## Risks

- A render that hangs or eats memory: killed at the timeout (subprocess), worker has memory limits.
- A hostile Markdown reaching WeasyPrint: layered defence (no raw HTML, no images, link allowlist, deny-all fetcher, counted); each layer has its own test and a mutation check.
- `document_search` interrupt re-runs its node on resume: nothing with side effects before `interrupt()`.
- Migration on a running DB: additive table only.
- Cost: worst case is bounded by the 4k output limit and the 16k token material cap.

## Ready to implement?

Yes (the developer reviews the scratch file as the work goes; nothing here is destructive).
