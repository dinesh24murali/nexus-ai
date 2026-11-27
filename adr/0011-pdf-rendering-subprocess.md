# ADR-0011: Render PDFs in a Killable Child Process With a Deny-All URL Fetcher

**Status:** Accepted
**Date:** 2026-11-22
**Deciders:** Nexus AI Team

---

## Context

TASK-041 turns conversations, answers and web results into PDFs. The text is untrusted (a user pasted it, a web page wrote it, a model summarised it), and the renderer, WeasyPrint, is a layout engine that fetches whatever a page points at: `file://`, `http://169.254.169.254/...`, `data:` URLs, `@import`ed stylesheets. A document that makes it load a local file or an internal URL puts that content into a PDF the user downloads (doc 10 LLM-T18). The renderer is also CPU-bound C code (Pango, Cairo) that a Python thread cannot interrupt, so a pathological document would keep a thread busy after any timeout, and the 60 s limit in doc 08 section 9 would be a promise nothing keeps.

## Decision

1. **Sanitise first** (`documents/markup.py`): raw HTML is escaped, images are not rendered, only `http(s)` links survive and are printed in full, an allowlist sanitiser (`nh3`) is the last pass.
2. **Deny-all URL fetcher** (`documents/fetcher.py`): the renderer is given a fetcher that serves one bundled stylesheet (`nexus-bundled:document.css`) and refuses every other request, whatever its scheme. Each refusal is counted (`documents.external_fetches`, an absolute eval gate of 0) and never logs the URL.
3. **A child process, not a thread** (`documents/render.py`, `worker_main.py`): the job starts `python -m app.documents.worker_main`, feeds it the HTML on stdin and kills it at 50 s (the job's own limit is 60 s). The child gets a minimal environment (no API keys, no database URL), writes to a `.part` file that is moved into place only after success, and refuses documents over 30 pages with a distinct exit code. Failures reach the user as fixed reasons; the child's stderr is discarded because it can echo document text.
4. **Storage under the user's own directory** (`<UPLOADS_DIR>/<user id>/documents/<id>.pdf`), so account deletion (`purge_user`) removes PDFs together with uploads.

## Consequences

### Positive
- Three independent layers; each has its own test and a mutation check (letting the fetcher reach the network fails 5 tests).
- The time limit is real: a render that hangs is killed, and a cancelled job kills its child.
- A document that even tried to reach out is visible as a metric, not silently dropped.

### Negative
- About one second of process and Pango start-up per document, paid inside a 60 s job: acceptable for a background render.
- The image needs system libraries (Pango, HarfBuzz subset, fontconfig, DejaVu), and the renderer is one more thing the worker's memory limit has to cover.
- A child process shares the worker's container: it is not a sandbox against code execution in WeasyPrint itself. The worker runs without capabilities and with memory and process limits (TASK-030); egress filtering is still D22.

### Neutral
- The sanitiser mirrors the client's Markdown rules (no raw HTML, no images, only web links) but is a separate implementation.

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| WeasyPrint in a worker thread with `asyncio.wait_for` | A thread cannot be killed: the timeout would only stop waiting, and a hostile document could pin a core indefinitely |
| A headless browser (Chromium/Playwright) to print | Far heavier image and attack surface, and it fetches the network by default |
| ReportLab / fpdf2 (no HTML) | No Markdown-to-layout engine; every table, list and code block would be hand-built |
| Trust the sanitiser alone | One bypass would turn into a file read or an SSRF; the fetcher costs nothing at runtime |

## References

- doc 08 sections 2.5, 5, 9; doc 10 LLM-T18; doc 09 `documents.external_fetches`, rt-23; ADR-0007 (job queue)
