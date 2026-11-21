# Product Requirements Document — Nexus AI

**Version:** 1.3 · **Date:** 2026-11-21 (task 003b: v1.1 trimmed — schedules to backlog, fast path replaced by `/help`, `remember_fact` removed) · previous 1.2 2026-11-21 (v1.1 scope), 1.1 2026-11-19 (task 003) · **Owner:** Nexus AI Team · **Status:** Revised
**Source:** `docs/00_intake/intake_questionnaire.md`

---

## 1. Executive Summary

Nexus AI is a self-hostable, ChatGPT-style assistant for individual knowledge workers. It streams answers, grounds them in the user's own uploaded documents with citations, uses tools (web search, calculator) with the user's approval, and remembers user preferences across conversations, in a way users can fully inspect and control.

**v1.1 scope:** an **intent router** sends each message to one of four pipelines: general chat, memory commands, **PDF documents** (the current thread, the last answer, or a news brief on a topic) and **deep research** (a multi-search cited report). Slash commands force a pipeline without the router. Scheduled tasks are in the backlog (§13). See §7.7 and ADR-0010.

**What it is:** a **reference build** that the developer runs themselves, with an **invite-only** hosted instance for a few people. "Production-grade" means engineering discipline — evals as quality gates, tracing, tests, tenant isolation, guardrails and cost caps — not an operated public service with an SLO. It is built by one developer working part-time with AI coding agents.

## 2. Problem Statement

- **Current pain:** Hosted assistants are black boxes. Users can't see or edit what is remembered about them, can't easily ask questions over private files without uploading them to a third-party product, and can't self-host. Engineers building LLM apps lack a small, end-to-end, production-quality reference that includes evals, tracing and safety.
- **Root cause:** Consumer assistants optimise for breadth and lock-in, and most open-source chat UIs are demos with no quality gates, tenant isolation or cost control.
- **Impact:** Users either avoid AI for private work or leak data into tools they don't control; teams ship LLM features that regress silently and overspend.

## 3. Goals & Success Metrics

| ID | Goal | KPI | Target |
|----|------|-----|--------|
| G1 | Users find answers useful (**advisory** — few invited users, so not a gate) | % of rated answers thumbs-up | ≥ 70% |
| G2 | Grounded document answers | RAG faithfulness (offline eval) | ≥ 0.85 |
| G3 | Responsive experience | p95 time-to-first-token (no tools) | < 2 s |
| G4 | Affordable to run | Average LLM cost per chat message (cache-friendly prompt layout, models per 08 §3). Research and document runs have their own per-run caps (§7.7) | < $0.01 |
| G5 | Trustworthy with data | Cross-user leakage incidents (evals + prod) | 0 |
| G8 | Easy to run as a reference | Fresh clone → working local stack by following the README | ≤ 15 min |
| G9 | Quality is gated, not hoped for | AI features shipped with their eval suite and gate | 100% |
| G10 | Requests reach the right pipeline | Intent-router accuracy on `router.intents` (offline eval) | ≥ 0.92 |

G6 (adoption) and G7 (availability SLO) were dropped in revision 1.1: the hosted instance is invite-only and has no SLO. Quality targets are **targets**; CI gates on measured baselines that only ratchet up (ADR-0009).

## 4. Non-Goals

- Competing with frontier assistants on breadth (images, voice, plugins).
- Multi-tenant team/enterprise features (sharing, SSO, admin console).
- Supporting multiple LLM providers in v1 (the code keeps a seam for it).

## 5. User Personas

**P1 — Priya, data analyst.** Uploads reports and spreadsheets-as-PDF, asks questions across them. *Needs:* accurate answers with sources. *Pain:* hallucinated numbers, no citations.

**P2 — Marco, software developer.** Uses chat for coding help and quick web lookups; wants it to remember his stack. *Needs:* fast streaming, good code rendering, persistent preferences. *Pain:* re-explaining context every conversation.

**P3 — Sam, AI engineer / self-hoster.** Runs Nexus AI on a VM with their own OpenAI key and studies its architecture. *Needs:* one-command deploy, traces, evals, clear docs. *Pain:* demo-grade OSS apps without observability or tests.

## 6. User Stories

**Accounts**
- US-01 As an invited user, I want to sign up with my invite code, email and password so that my conversations are private to me.
- US-02 As any user, I want to delete my account and all my data so that I stay in control of my information.

**Core chat**
- US-03 As Marco, I want answers to stream token by token so that I can start reading immediately.
- US-04 As Marco, I want my conversations listed in a sidebar with auto-generated titles so that I can return to them.
- US-05 As Marco, I want to stop a response mid-stream and regenerate it so that I don't waste time on bad answers.
- US-06 As Marco, I want markdown and syntax-highlighted code blocks with a copy button so that I can use code directly.
- US-07 As any user, I want to rename and delete conversations so that my history stays organised.

**Files & RAG**
- US-08 As Priya, I want to upload PDF/DOCX/TXT/MD files and see their processing status so that I know when they're searchable.
- US-09 As Priya, I want answers about my files to cite the file and page so that I can verify them.
- US-10 As Priya, I want to delete a file and have it removed from search so that outdated data isn't used.

**Tools**
- US-11 As Marco, I want the assistant to search the web for current information, after I approve the search, so that I stay in control of outbound requests.
- US-12 As Priya, I want exact arithmetic via a calculator tool so that numbers aren't hallucinated.
- US-13 As any user, I want to see which tools were used and with what inputs so that I understand how an answer was produced.

**Memory**
- US-14 As Marco, I want the assistant to remember stable facts I share (e.g. "I use Python") so that I don't repeat myself.
- US-15 As any user, I want to view, edit and delete memories, or turn memory off, so that I control what is stored.

**Feedback & limits**
- US-16 As any user, I want to rate answers thumbs up/down with an optional comment so that quality improves.
- US-17 As Sam, I want per-user and global daily spending limits so that nobody can run up the bill.
- US-18 As Sam, I want to invite people from the command line so that only people I choose can register.

**Routing & generated outputs (v1.1)**
- US-19 As Priya, I want to ask for "a PDF summary of this chat" and download it so that I can share the outcome with colleagues who don't use Nexus.
- US-20 As Marco, I want a PDF news brief on a topic ("latest on the EU AI Act") with sources so that I can read it offline.
- US-21 As Priya, I want to ask for in-depth research on a question, approve the search plan, and get a cited report so that I can trust where the claims come from.
- US-23 As any user, I want to manage memories by chat ("what do you know about me?", "forget where I work") so that I don't have to open Settings.
- US-24 As any user, I want to see which mode handled my message and force one with a slash command (`/pdf`, `/brief`, `/research`, `/memory`, `/help`) so that I stay in control when the assistant guesses wrong.

(US-22, scheduled news briefs, moved to the backlog in revision 1.3: §13.)

## 7. Functional Requirements

### 7.1 Accounts & Auth
- FR-001 Users register with email + password (min 10 chars, checked against a common-password list). With `REGISTRATION_MODE=invite` (the default) registration also requires a valid **invite code**; `open` disables the check for self-hosters who want it.
- FR-006 Invite codes are single-use and expire (default 7 days). They are created, listed and revoked with the admin CLI (`python -m app.cli invite create|list|revoke`).
- FR-007 Admin actions are CLI-only in v1: invites, password reset, disable/enable a user. There is no admin UI.
- FR-002 Login issues a 15-min JWT access token and a 7-day rotating refresh token in an httpOnly, Secure, SameSite=Strict cookie.
- FR-003 Logout revokes the refresh token; reuse of a revoked refresh token revokes the whole token family.
- FR-004 Users can permanently delete their account; all Postgres rows, Qdrant points, files, memories and checkpoints are erased within 24 h.
- FR-005 *(Deferred to after v1.)* Users can export their data (conversations, memories, file list) as JSON.

### 7.2 Conversations & Chat
- FR-010 Users create, list (cursor-paginated, newest first), rename and delete conversations.
- FR-011 Sending a message streams the assistant reply over Server-Sent Events.
- FR-012 Stream events include tokens, tool start/end, citations, interrupts, usage and completion.
- FR-013 Users can cancel an in-progress response; the partial answer is kept and marked `cancelled`.
- FR-018 A response interrupted by a server restart is saved as `failed` with its partial text, and the conversation is usable again within about a minute.
- FR-014 Users can regenerate the last assistant response; the previous version is replaced in the thread.
- FR-015 A conversation title is generated after the first exchange (≤ 60 chars).
- FR-016 Long conversations are summarised automatically so context stays within budget.
- FR-017 Assistant output renders markdown (sanitised), code blocks with highlighting and copy.

### 7.3 Files & RAG
- FR-020 Upload PDF, DOCX, TXT, MD up to 20 MB; max 50 files per user; duplicates (same SHA-256) are rejected.
- FR-021 Files are processed asynchronously (`pending → processing → ready | failed`) and status is visible.
- FR-022 Ready files are searchable by the `search_my_files` tool, scoped strictly to the owner.
- FR-023 Answers using file content include citations (file name, page, snippet).
- FR-024 Deleting a file removes its chunks from the vector index before the delete returns success.

### 7.4 Tools & Human-in-the-Loop
- FR-030 The agent can call `search_my_files`, `web_search`, `calculator`. It has no tool that writes memory (FR-040, FR-065).
- FR-031 `web_search` pauses the run and asks the user to approve, edit or reject the query.
- FR-032 Pending approvals survive page reloads and expire after 24 h.
- FR-033 Every tool call is shown in the UI with name, arguments and status.
- FR-034 The agent makes at most 6 tool calls per turn.
- FR-035 *(Removed in revision 1.3.)* The `remember_fact` tool and its approval rule were dropped: long-term memory is written only by background extraction from the user's own message (FR-040) and by memory commands (FR-065), so file passages and web results can never write to memory.

### 7.5 Long-Term Memory
- FR-040 After each exchange, stable user facts are extracted in the background and stored per user (this also covers "remember that …" in v1). Facts similar to an existing memory are not stored twice.
- FR-041 Relevant memories are injected into the system context at the start of each run.
- FR-042 Users can list, edit, delete individual memories, clear all, and toggle memory on/off.
- FR-043 When memory is off, nothing is extracted and nothing is injected.

### 7.6 Feedback, Usage & Safety
- FR-050 Users can rate any assistant message (+1/−1, optional comment ≤ 1000 chars); ratings go to Langfuse as scores.
- FR-051 Limits: a per-user daily **spend** budget (default $0.50, env-configurable), a **global** daily spend cap (default $5) that blocks new chat runs for everyone once reached, and 30 messages/min per user; exceeding them returns a clear error. Spend is computed from token usage and the pricing table.
- FR-052 User input is checked by OpenAI moderation; flagged input is refused with an explanation. If the moderation API is unavailable, input is allowed (fail-open), logged and tagged in the trace.
- FR-053 Users can see their spend and message count for today against their budget.

### 7.7 Intent Routing & Generated Outputs (v1.1)
- FR-060 Every new user message without a slash command is classified into one intent: `general_chat`, `manage_memory`, `generate_document` or `deep_research`. Low confidence (below a threshold tuned on the router eval set, starting at 0.70), a router error or a timeout falls back to `general_chat` (v1 behaviour). Resuming an approval never re-routes. The router sees only the user's own recent messages.
- FR-061 The UI shows which mode handled each message. A slash command at the start of a message forces the mode and skips the router: `/pdf` (this conversation), `/pdf last` (the last answer), `/brief <topic>`, `/research <question>`, `/memory`, `/memory remember <text>`, `/memory forget <text>`, `/help`. Commands are parsed without a model; a malformed command gets a usage reply. An operator kill switch (`ROUTER_DISABLED`, a runtime flag) sends everything to `general_chat`.
- FR-062 **PDF documents.** Users can generate a PDF from (a) the current conversation (summary, decisions, open questions), (b) the last answer, as written (no extra model call), or (c) a topic news brief built from web search (the query needs approval, like FR-031). The PDF has a title, date, sections and numbered sources. It appears as a download card on the assistant message and in a Documents list. Limits: 10 documents per user per day, ≤ 30 pages, the 100 most recent are kept per user; a run's worst-case cost is ≤ $0.05 and it doesn't start if the user's remaining budget or the global cap is lower. Users can delete documents.
- FR-063 **Deep research.** The assistant proposes a research plan of 3–6 search queries, which the user approves, edits or rejects with **one** approval. Only approved queries run. Progress is streamed. The result is a cited report (≤ 1,500 words) in chat; if a PDF was asked for, the report is also rendered as one. Token limits keep a run's worst-case cost ≤ $0.05; the run doesn't start if the user's remaining daily budget or the global cap is lower, and a started run is never cut off.
- FR-064 *(Moved to the backlog in revision 1.3: §13.)* Scheduled tasks.
- FR-065 **Memory by chat.** "What do you know about me?" lists memories; "remember X" saves; "forget X" shows the matching memory (or up to 3 candidates) in a confirmation card and deletes only the one the user approves. Respects memory on/off, the sensitive-category deny-list and the instruction filter.
- FR-066 **Help.** `/help` returns a fixed list of capabilities and slash commands without a model call. "What can you do?" in chat is answered from the same list, which is part of the system prompt. Regenerate is offered only on general-chat answers.

## 8. Non-Functional Requirements

| Area | Requirement |
|------|-------------|
| Performance | TTFT < 2 s p95 (no tools; from v1.1 it includes the intent router); full answer < 20 s p95 excluding approval wait; non-streaming API p95 < 300 ms |
| Scalability | Invite-only (≈ 10–20 users); load-tested at 20 concurrent streams + 5 rps against the fake LLM on one VM (4 vCPU / 8 GB); stateless API so it can scale horizontally later |
| Availability | Best-effort, **no SLO**. Graceful degradation when the OpenAI API is down (clear error, fallback model); deploys and crashes never leave a conversation locked (run lease + reaper); one uptime check on `/readyz` |
| Security | OWASP ASVS L1, OWASP LLM Top 10 mitigations (doc 10), tenant isolation tested, secrets only via env |
| Privacy | PII never in logs; Langfuse traces redacted before leaving the VM (Langfuse Cloud, EU region), kept 30 days; account deletion within 24 h; short privacy section in the README |
| Quality | Eval gate (`make eval`) blocks regressions against measured baselines and absolute safety gates (ADR-0009) |
| Observability | Every run traced in Langfuse; structured JSON logs with request id ↔ trace id |
| Cost | < $0.01 average per message, tracked per message and per user/day in USD |
| Accessibility | WCAG 2.1 AA for the web UI |
| Maintainability | Coverage targets: backend 80%, frontend 70%; measured coverage only ratchets up and reaches the targets by M6 (ADR-0009) |
| Recoverability | Nightly `pg_dump` + Qdrant snapshot off-site; restore tested once |

## 9. Assumptions

> **Assumption:** Users bring content in English; other languages work but are not evaluated in v1.
> **Assumption:** One OpenAI organisation key per deployment; users don't bring their own keys.
> **Assumption:** Files are stored on the VM's disk (Docker volume); object storage is a later change.
> **Assumption:** A single VM is sufficient for v1 load; no Kubernetes.
> **Assumption:** Tavily is acceptable as the web search provider.
> **Assumption:** Email verification and password reset by email are deferred (no email provider in v1); password reset is done by an admin CLI.
> **Assumption:** Langfuse Cloud (EU region, Hobby plan) is acceptable for redacted traces; self-hosters can run Langfuse v4 themselves (ADR-0004).
> **Assumption:** One developer working part-time with AI coding agents; AI agents draft eval dataset records and the developer reviews them.

## 10. Open Questions

> **Open question:** Production domain for the hosted instance.

Resolved in revision 1.1: no staging environment (CI acts as staging); models chosen per 08 §3 with an M2 bake-off for the chat model; admin is CLI-only; registration is invite-only.

## 11. Out of Scope (v1)

Image generation and image/voice input; code execution sandbox; scheduled tasks (backlog, §13); editing generated PDFs; team workspaces and conversation sharing; custom assistants; plugin/MCP marketplace; mobile apps; multiple LLM providers; billing; OAuth/SSO; email notifications; admin UI; data export (FR-005, deferred); availability SLO; Prometheus metrics (deferred).

## 12. Milestones (order, not dates)

Milestones set the order of work; there are no dates. Each milestone ends **deployed** to the hosted instance.

| Milestone | Contents |
|-----------|----------|
| M0 Docs | Phase 1 docs, ADRs, plan revision (tasks 002–003) |
| M1 Skeleton | Compose stack, auth + invites, DB, React shell, CI, deploy workflow, backups — live on the VM |
| M2 Core chat + evals | Streaming chat, threads, tracing, fake LLM, eval harness, chat-model bake-off, limits and moderation |
| M3 RAG | Upload, ingestion, citations, RAG evals |
| M4 Tools + HITL | Web search approval, calculator, tool evals |
| M5 Memory | Extraction, memory in context, memories UI, memory evals |
| M6 Hardening | Full red-team set, account purge test, security review, mock-LLM load test, coverage targets |
| M7 Routing & outputs (v1.1) | Intent router + router evals, slash commands and `/help`, memory by chat, PDF documents, deep research, new red-team cases (tasks 011–012) |

## 13. Future Intents (backlog)

Candidates for later router intents, kept out of v1.1. Each needs its own eval suite and threat-model rows before it ships.

| Intent | Example | Notes |
|--------|---------|-------|
| `extract_structured` | "Pull every invoice total from my files into a CSV" | Map-reduce structured output over chunks; CSV/XLSX export |
| `compare_documents` | "What changed between contract v1 and v2?" | Two-file scoped retrieval; builds on file targeting (doc 08 §6) |
| `recall_history` | "What did we decide last week about the DB?" | New index over past conversations |
| `analyze_data` | "Chart monthly totals from sales.csv" | Code-execution sandbox; largest security cost, so last |
| `study_aid` | "Make 10 flashcards from chapter 3" | Fixed JSON schema rendered as cards |
| `diagram` | "Draw this flow as a diagram" | Mermaid output rendered client-side |
| `schedule_task` | "Every Monday at 8, send me a news brief on Rust" (US-22, FR-064) | Moved out of v1.1 in revision 1.3: with in-app delivery only, a schedule saves one message. Revisit together with email/push delivery. The revision-1.2 design (approval with the exact query, 5 active per user, daily minimum, worker runs under the run lease) is in git history (commit `8be9349`); it must also cap scheduled spend so schedules can't use up the global daily cap |
