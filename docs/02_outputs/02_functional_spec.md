# Functional Specification — Nexus AI

**Version:** 1.3 · **Date:** 2026-11-21 (task 003b: schedules to backlog, slash-command grammar, forget confirmation, `remember_fact` removed) · previous 1.2 2026-11-21 (v1.1), 1.1 2026-11-19 (task 003) · **PRD:** [`01_prd.md`](01_prd.md) · **API:** [`04_api_spec.yaml`](04_api_spec.yaml)

## 1. Overview

This document specifies user-visible behaviour of Nexus AI v1: flows, screens, per-feature behaviour, state machines, permissions and data retention. Requirement IDs (FR-xxx) refer to the PRD. Endpoint names refer to the OpenAPI spec, which is authoritative for request/response shapes.

## 2. User Flows

**UF-001 Register (with invite) & first chat**
1. The admin creates an invite with the CLI and sends the link `/register?invite=<code>` to the person.
2. User opens the link (the code is prefilled; it can also be typed), enters email, password, display name.
3. On success they land on `/` (new chat) with an empty-state screen showing example prompts.
4. User types a message and presses Enter → a conversation is created and the URL becomes `/c/{id}`.
5. Answer streams; after completion the sidebar shows the auto-generated title.

**UF-002 Continue a conversation**
1. User clicks a conversation in the sidebar → `/c/{id}` loads the message history (latest 50, older on scroll-up).
2. User sends a message; the answer streams below.

**UF-003 Stop & regenerate**
1. While streaming, the Send button becomes Stop. Clicking it cancels the run; the partial answer stays with a "Stopped" label.
2. On the last assistant message, the user clicks Regenerate → the message is replaced by a new streamed answer.

**UF-004 Upload a file and ask about it**
1. User clicks the paperclip in the composer (or goes to `/files`) and selects a file.
2. A chip shows upload progress, then `Processing…`, then `Ready` (polling every 2 s while not final).
3. User asks "What does the Q3 report say about churn?" → the assistant calls `search_my_files`; a tool card shows "Searching your files".
4. Answer streams with citation chips `[1] q3-report.pdf p.4`; clicking a chip shows the snippet in a popover.

**UF-005 Approve a web search**
1. User asks about current events → the stream emits an `interrupt`; an approval card shows the proposed query with Approve / Edit / Reject.
2. Approve → run resumes, the tool card shows results count, the answer streams with web citations.
3. Edit → user changes the query, then Approve. Reject → the assistant answers without searching and says so.
4. If the user reloads, the pending approval card is restored from the conversation state.

**UF-006 Memory**
1. User says "I mostly write Go and prefer concise answers."
2. After the turn, a subtle toast says "Memory updated" with a link to Settings → Memory.
3. In a new conversation the assistant answers in a concise style and assumes Go.
4. In Settings → Memory, user edits or deletes the memory, or turns memory off.
5. "Remember that I use Go" works the same way: the background extraction saves it after the turn. To delete a memory by chat, v1 points the user to Settings → Memory; v1.1 adds `/memory forget …` (UF-012).

**UF-009 Generate a PDF (v1.1)**
1. User types "Turn this conversation into a PDF" (or `/pdf`). A small mode chip on the answer reads "Document". "Make that a PDF" (or `/pdf last`) instead renders the last answer as written, with no extra model call.
2. A progress line shows "Writing…" then "Rendering PDF…"; a document card appears with title, page count and a Download button (it shows "Preparing…" while the status is `pending`, polling `GET /documents/{id}` every 2 s).
3. For "PDF news brief on <topic>" (or `/brief <topic>`), the web-search approval card (UF-005) appears first; after approval the same flow follows.
4. The document is also listed on `/documents`.

**UF-010 Deep research (v1.1)**
1. User types "Research <question> in depth" (or `/research <question>`).
2. An approval card "Research plan" lists 3–6 queries, each editable, with Approve / Edit / Reject.
3. After approval, progress shows "Searching 3/5" then "Writing report"; the cited report streams in. "…as a PDF" also renders the report as a PDF and shows a document card after it (`/pdf last` does the same afterwards).

**UF-012 Memory by chat (v1.1)**
1. "What do you know about me?" → a list of memories with a link to Settings → Memory.
2. "Forget where I work" (or `/memory forget where I work`) → a confirmation card shows the matching memory, or up to 3 candidates to pick from. Approve deletes exactly that one and confirms with its text; Reject keeps everything.

**UF-007 Feedback**
1. User hovers an assistant message, clicks 👍 or 👎; 👎 opens an optional comment box.

**UF-008 Delete account**
1. Settings → Account → Delete account → confirm by typing email → user is logged out; data is erased within 24 h.

## 3. Screen / Page Inventory

| Route | Purpose | Key UI elements | Auth |
|-------|---------|-----------------|------|
| `/login` | Sign in | Email, password, submit, link to register, error banner | Public (redirect to `/` if logged in) |
| `/register` | Create account | Invite code (prefilled from `?invite=`), email, display name, password + strength hint, submit. With `REGISTRATION_MODE=open` the invite field is hidden | Public |
| `/` | New chat | Sidebar (conversations, New chat, Files, Settings, usage meter), empty state with example prompts, composer | Authenticated |
| `/c/:conversationId` | Conversation | Sidebar, message list (markdown, code blocks, tool cards, citation chips, approval cards, feedback buttons), composer (attach, send/stop), regenerate on last answer | Authenticated (owner only) |
| `/documents` | Generated documents (v1.1) | Table (title, kind, pages, created, conversation link, download, delete), limit hint (10/day) | Authenticated |
| `/files` | Manage files | Upload dropzone, table (name, size, status, chunks, uploaded at, delete), limits indicator (n/50) | Authenticated |
| `/settings/account` | Profile & account | Display name, email (read-only), change password, delete account | Authenticated |
| `/settings/memory` | Memory control | Toggle memory on/off, list memories (edit inline, delete), Clear all | Authenticated |
| `/settings/usage` | Usage | Spend today vs. daily budget (USD), tokens and messages today | Authenticated |
| `/settings/appearance` | Theme | Light / Dark / System | Authenticated |
| `*` | 404 | Message + link home | Public |

## 4. Feature Specifications

### FS-001 Authentication & invites (FR-001…004, FR-006, FR-007)
- **Trigger:** `/register`, `/login`; token refresh happens silently when an API call returns 401 with `token_expired`.
- **Happy path:** register with a valid invite → invite marked used → logged in; access token kept in memory only; refresh cookie set by API.
- **Validation:** invite code required when `REGISTRATION_MODE=invite` (default); email RFC 5322 and ≤ 254 chars, lower-cased; password 10–128 chars, not in common-password list; display name 1–60 chars.
- **Invites:** created by `python -m app.cli invite create [--expires 7d] [--note "..."]`; single-use; listed and revoked with `invite list` / `invite revoke <code>`. Codes are random (≥ 128 bits), stored hashed.
- **Errors:** `INVITE_INVALID` "This invite code is invalid, used or expired." (same message for every case); `EMAIL_TAKEN` "An account with this email already exists."; `INVALID_CREDENTIALS` "Email or password is incorrect." (no user enumeration); `RATE_LIMITED` after 10 failed logins / 15 min per IP+email, or 10 failed invite attempts / 15 min per IP.
- **Edge cases:** refresh token reuse → all sessions revoked, user sent to `/login` with "Your session expired. Please sign in again."

### FS-002 Conversations (FR-010, FR-015)
- **Trigger:** first message on `/`; sidebar actions (rename, delete via ⋯ menu).
- **Happy path:** list newest-first, 30 per page, infinite scroll.
- **Validation:** title 1–100 chars.
- **Errors:** delete of a conversation with an active run cancels the run first; 404 for others' conversations (never 403, to avoid enumeration).
- **Edge cases:** title generation fails → title stays "New chat"; retried once in the background.

### FS-003 Streaming chat (FR-011…014, FR-016, FR-017)
- **Trigger:** Send (Enter; Shift+Enter for newline).
- **Happy path:** `POST /conversations/{id}/messages` opens an SSE stream: `run_started` → `token`* → (`tool_start`/`tool_end`/`citation`)* → `usage` → `done`.
- **Validation:** message 1–16,000 chars; one active run per conversation (second send gets `RUN_IN_PROGRESS`).
- **Errors:** `MODERATION_FLAGGED` → inline notice, message not sent to model; `BUDGET_EXCEEDED` → banner with reset time (00:00 UTC), worded differently for the user budget and the global cap (`details.scope = user | global`); `SERVICE_RESTARTING` (503, during a deploy) → "Nexus is restarting, try again in a moment"; `LLM_UNAVAILABLE` after retries + fallback → assistant bubble with "The model is unavailable right now. Try again." and a Retry button; network drop → client shows "Connection lost" with Retry (the run continues server-side and its result appears on reload).
- **Edge cases:** cancel → `done` with `finish_reason=cancelled`; regenerate only on the latest assistant message, and only when it is a general-chat answer (v1.1); context too long → older turns summarised automatically (invisible to user); server restart mid-answer → the partial answer is saved with status `failed` and an "Interrupted" label with Retry, and the conversation unlocks within about a minute (run lease + reaper).

### FS-004 File upload & RAG (FR-020…024)
- **Trigger:** paperclip or `/files` dropzone (multi-select, max 5 at once).
- **Validation:** extension and sniffed MIME in {pdf, docx, txt, md}; ≤ 20 MB; ≤ 50 files/user; duplicate SHA-256 → `FILE_DUPLICATE`.
- **Errors:** `FILE_TOO_LARGE`, `FILE_TYPE_UNSUPPORTED`, `FILE_LIMIT_REACHED`; processing failure → status `failed` with reason ("No extractable text — scanned PDFs are not supported yet", "File is password-protected").
- **Edge cases:** user asks about a file still processing → the assistant says it is not ready yet; deleting a file mid-processing cancels the job and cleans up partial chunks.

### FS-005 Tools & approval (FR-030…034)
- **Trigger:** model decides to call a tool.
- **Happy path:** tool card shows name, args, spinner → result summary. `web_search` always emits `interrupt` and waits for `POST /conversations/{id}/runs/resume`. If the same model message also asks for other tools, none of them run until the decision; then all run together (a rejected search gets "user declined").
- **Validation:** edited query 1–400 chars.
- **Errors:** tool timeout (10 s) → card shows "Search timed out"; the model continues without it. Approval expired (> 24 h) → card shows "Expired"; sending a new message abandons the pending run.
- **Edge cases:** the cap counts individual calls: calls beyond the 6th get "tool limit reached", the loop stops and the model must answer with what it has.

### FS-006 Memory (FR-040…043)
- **Trigger:** background extraction after each completed turn (only if memory is enabled).
- **Happy path:** up to 3 new facts per turn; duplicates merged; toast "Memory updated".
- **Validation:** memory text 1–500 chars; max 200 memories per user (oldest least-used evicted, with notice).
- **Errors:** extraction failure is silent to the user (logged and traced).
- **Edge cases:** user says "forget that I live in Berlin" → v1: the assistant says memories are deleted in Settings → Memory and never claims to have forgotten; v1.1: routed to memory by chat (FS-012). Sensitive categories (health, religion, sexual orientation, precise location, credentials) and instruction-like text ("from now on you…") are never stored. A fact similar (≥ 0.9) to an existing memory is not stored twice.

### FS-009 Intent routing (FR-060, FR-061, FR-066)
- **Trigger:** every new user message (not resumes).
- **Happy path:** `route_selected` arrives before any tokens; the answer shows a small mode chip (Chat, Memory, Document, Research) with a tooltip "Wrong mode? Use /pdf, /brief, /research, /memory".
- **Slash commands:** typing `/` in the composer opens a menu that inserts the command text. The backend parses a command at the very start of the message, case-insensitively and without a model: `/pdf`, `/pdf last`, `/brief <topic>`, `/research <question>`, `/memory`, `/memory remember <text>`, `/memory forget <text>`, `/help` (doc 08 §2.6). `/help` replies with a fixed capability-and-commands list.
- **Errors:** router failure or low confidence is invisible (falls back to Chat); a malformed command gets a usage reply for that command; a command for a disabled mode gets a short explanation.

### FS-010 Documents (FR-062)
- **Trigger:** `generate_document` intent, `/pdf`, `/pdf last` or `/brief <topic>`.
- **Sources:** this conversation (its summary plus the most recent messages, up to 16k tokens); the last answer, rendered as written (no model call); a topic news brief (one approved web search).
- **Validation:** 10 per user per UTC day; ≤ 30 pages (longer drafts are shortened by the model, then truncated with a note); the 100 most recent are kept; the run's worst-case cost (≈ $0.04) must fit the remaining user budget and the global cap.
- **Errors:** limit or budget reached → assistant explains and shows reset time; render failure → card "Couldn't create the PDF" + Retry (re-enqueues render); empty thread → "There's nothing to summarise yet"; `/pdf last` with no previous answer → "There's no answer to turn into a PDF yet".
- **Cancel / regenerate:** cancel before the document is saved creates nothing; once rendering is queued the document finishes. No Regenerate button on document messages.
- **Edge cases:** the conversation is deleted → the document stays in `/documents` without a conversation link; account deletion removes all PDFs.

### FS-011 Deep research (FR-063)
- **Trigger:** `deep_research` intent or `/research <question>`.
- **Validation:** plan of 1–6 queries, each 1–400 chars (edited queries too); the run's worst-case cost (≈ $0.05) must fit the remaining user budget and the global cap.
- **Errors:** budget too low → explained, nothing runs; a search times out → the report notes the missing source; reject → "OK, I won't search" with no report.
- **Cancel / regenerate:** cancel before the report is written → no report, and the stopped message lists the searches already done. No Regenerate button on research messages; the user asks again.

### FS-012 Memory by chat (FR-065)
- **Trigger:** `manage_memory` intent or `/memory`, `/memory remember <text>`, `/memory forget <text>`.
- **List:** the user's memories as a list with a link to Settings → Memory (no model call).
- **Save:** the same deny-list and instruction filter as extraction; memory off → "Memory is turned off" with a link to Settings. Background extraction is skipped for this turn.
- **Forget:** searches the user's memories (top 3, similarity ≥ 0.75). No match → "I couldn't find a memory about that". Otherwise a confirmation card shows the one clear match, or up to 3 candidates to pick from; Approve deletes exactly one and confirms with its text; Reject or expiry (24 h) deletes nothing.
- **Regenerate:** not offered on memory messages.

### FS-007 Feedback (FR-050)
- One rating per user per message (re-rating overwrites); comment ≤ 1000 chars.

### FS-008 Usage limits (FR-051, FR-053)
- Two spend limits in USD, computed from token usage × the pricing table: per user per UTC day (default $0.50) and global per UTC day (default $5). Plus 30 messages/min per user.
- Both budgets are checked before a run starts and updated after it ends; a run in progress is never cut off for budget reasons.
- Background jobs (titles, memory extraction) count toward the user's spend.

## 5. State Diagrams

**File processing**
```mermaid
stateDiagram-v2
  [*] --> pending: upload accepted
  pending --> processing: worker picks job
  processing --> ready: chunks indexed
  processing --> failed: parse/embed error (after 3 attempts)
  pending --> deleted: user deletes
  processing --> deleted: user deletes (job cancelled, chunks purged)
  ready --> deleted: user deletes (points purged first)
  failed --> deleted
  deleted --> [*]
```

**Assistant run**
```mermaid
stateDiagram-v2
  [*] --> running: message sent
  running --> awaiting_approval: approval interrupt (web_search, research_plan, forget_memory)
  awaiting_approval --> running: approve / edit / reject
  awaiting_approval --> expired: 24 h elapsed
  running --> completed: done
  running --> cancelled: user stop
  running --> failed: unrecoverable error
  running --> failed: server restart (lease expired, reaper)
  completed --> [*]
  cancelled --> [*]
  failed --> [*]
  expired --> [*]
```

**Account**
```mermaid
stateDiagram-v2
  [*] --> active: register
  active --> pending_deletion: user deletes account
  pending_deletion --> [*]: purge job completes (≤ 24 h)
```

## 6. Notifications & Emails

No emails in v1. In-app only:

| Notification | Trigger | Content |
|--------------|---------|---------|
| Toast "Memory updated" | Memory extraction stored ≥ 1 fact | "Nexus remembered N thing(s). Manage" |
| Toast "File ready" / "File failed" | File status changes while user is in the app | File name + status/reason |
| Banner "Daily limit reached" | `BUDGET_EXCEEDED` | Which limit (yours / the instance's) and reset time (UTC) |
| Banner "Session expired" | Refresh fails | Prompt to sign in |

## 7. Permissions Matrix

Single role `user`; all resources are owner-scoped. Admin actions exist only as CLI commands on the server.

| Role | Action | Resource | Allow |
|------|--------|----------|-------|
| anonymous | register (with valid invite when `REGISTRATION_MODE=invite`) / login | auth | ✅ |
| anonymous | any other | any | ❌ (401) |
| user | create/read/update/delete | own conversations, messages, files, memories, feedback, documents | ✅ |
| user | any action | another user's resources | ❌ (404) |
| user | read | own usage | ✅ |
| user | delete | own account | ✅ |
| admin (CLI) | create / list / revoke invites; reset password; disable / enable user; purge data | invites, any user | ✅ |

## 8. Data Retention & Deletion

| Data | Retention | Deletion behaviour |
|------|-----------|-------------------|
| Account (email, password hash) | Until account deletion | Hard delete by purge job |
| Conversations, messages, checkpoints | Until user deletes conversation or account | Hard delete; checkpoints deleted by thread id |
| Uploaded files (disk) | Until user deletes file or account | File removed from disk + Qdrant points deleted |
| Vector chunks (Qdrant) | Same as file | Deleted by `file_id` filter (and by `user_id` on account deletion) |
| Memories | Until user deletes or account deletion | Hard delete from store |
| Generated documents (v1.1) | 100 most recent per user; until user deletes or account deletion | PDF removed from disk + row deleted; conversation deletion only unlinks |
| Feedback | Until account deletion | Hard delete; Langfuse scores remain anonymised (hashed user id) until trace expiry |
| Langfuse traces (Langfuse Cloud, EU region) | 30 days | Automatic expiry; user id is hashed, content redacted for PII before export |
| Invites | Until used, revoked or expired + 30 days | Hard delete by a periodic cleanup |
| Usage counters | 13 months (aggregates) | Deleted on account deletion |
| Application logs | 14 days | No message content or PII in logs |
