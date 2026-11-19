# Product Requirements Document — Nexus AI

**Version:** 1.0 · **Date:** 2026-11-19 · **Owner:** Nexus AI Team · **Status:** Draft for review
**Source:** `docs/00_intake/intake_questionnaire.md`

---

## 1. Executive Summary

Nexus AI is a self-hostable, ChatGPT-style assistant for individual knowledge workers. It streams answers, grounds them in the user's own uploaded documents with citations, uses tools (web search, calculator) with the user's approval, and remembers user preferences across conversations, in a way users can fully inspect and control. It is built to production standards (evals, tracing, guardrails, cost limits) so it doubles as a reference implementation for LLM applications.

## 2. Problem Statement

- **Current pain:** Hosted assistants are black boxes. Users can't see or edit what is remembered about them, can't easily ask questions over private files without uploading them to a third-party product, and can't self-host. Engineers building LLM apps lack a small, end-to-end, production-quality reference that includes evals, tracing and safety.
- **Root cause:** Consumer assistants optimise for breadth and lock-in, and most open-source chat UIs are demos with no quality gates, tenant isolation or cost control.
- **Impact:** Users either avoid AI for private work or leak data into tools they don't control; teams ship LLM features that regress silently and overspend.

## 3. Goals & Success Metrics

| ID | Goal (6 months after launch) | KPI | Target |
|----|------------------------------|-----|--------|
| G1 | Users find answers useful | % of rated answers thumbs-up | ≥ 70% |
| G2 | Grounded document answers | RAG faithfulness (offline eval) | ≥ 0.85 |
| G3 | Responsive experience | p95 time-to-first-token (no tools) | < 2 s |
| G4 | Affordable to run | Average LLM cost per message | < $0.01 |
| G5 | Trustworthy with data | Cross-user leakage incidents (evals + prod) | 0 |
| G6 | Adoption | Weekly active users on hosted instance | 100 |
| G7 | Reliability | Monthly availability | ≥ 99.5% |

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
- US-01 As any user, I want to sign up with email and password so that my conversations are private to me.
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
- US-17 As Sam, I want per-user daily token limits so that one user can't run up the bill.

## 7. Functional Requirements

### 7.1 Accounts & Auth
- FR-001 Users register with email + password (min 10 chars, checked against a common-password list).
- FR-002 Login issues a 15-min JWT access token and a 7-day rotating refresh token in an httpOnly, Secure, SameSite=Strict cookie.
- FR-003 Logout revokes the refresh token; reuse of a revoked refresh token revokes the whole token family.
- FR-004 Users can permanently delete their account; all Postgres rows, Qdrant points, files, memories and checkpoints are erased within 24 h.
- FR-005 Users can export their data (conversations, memories, file list) as JSON.

### 7.2 Conversations & Chat
- FR-010 Users create, list (cursor-paginated, newest first), rename and delete conversations.
- FR-011 Sending a message streams the assistant reply over Server-Sent Events.
- FR-012 Stream events include tokens, tool start/end, citations, interrupts, usage and completion.
- FR-013 Users can cancel an in-progress response; the partial answer is kept and marked `cancelled`.
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
- FR-030 The agent can call `search_my_files`, `web_search`, `calculator`, `remember_fact`.
- FR-031 `web_search` pauses the run and asks the user to approve, edit or reject the query.
- FR-032 Pending approvals survive page reloads and expire after 24 h.
- FR-033 Every tool call is shown in the UI with name, arguments and status.
- FR-034 The agent makes at most 6 tool calls per turn.

### 7.5 Long-Term Memory
- FR-040 After each exchange, stable user facts are extracted in the background and stored per user.
- FR-041 Relevant memories are injected into the system context at the start of each run.
- FR-042 Users can list, edit, delete individual memories, clear all, and toggle memory on/off.
- FR-043 When memory is off, nothing is extracted and nothing is injected.

### 7.6 Feedback, Usage & Safety
- FR-050 Users can rate any assistant message (+1/−1, optional comment ≤ 1000 chars); ratings go to Langfuse as scores.
- FR-051 Per-user limits: 200k tokens/day, 30 messages/min; exceeding them returns a clear error.
- FR-052 User input is checked by OpenAI moderation; flagged input is refused with an explanation.
- FR-053 Users can see their token usage for today.

## 8. Non-Functional Requirements

| Area | Requirement |
|------|-------------|
| Performance | TTFT < 2 s p95 (no tools); full answer < 20 s p95 excluding approval wait; non-streaming API p95 < 300 ms |
| Scalability | 100 users, 20 concurrent streams, 5 rps on one VM (4 vCPU / 8 GB); stateless API so it can scale horizontally later |
| Availability | 99.5% monthly; graceful degradation when the OpenAI API is down (clear error, fallback model) |
| Security | OWASP ASVS L1, OWASP LLM Top 10 mitigations (doc 10), tenant isolation tested, secrets only via env |
| Privacy | PII never in logs; Langfuse traces redacted and kept 30 days; account deletion within 24 h |
| Quality | Eval gate (`make eval`) blocks regressions against `evals/eval-baseline.json` |
| Observability | Every run traced in Langfuse; structured JSON logs with request id ↔ trace id |
| Cost | < $0.01 average per message, tracked per message and per user/day |
| Accessibility | WCAG 2.1 AA for the web UI |
| Maintainability | Backend coverage ≥ 80%, frontend ≥ 70% |

## 9. Assumptions

> **Assumption:** Users bring content in English; other languages work but are not evaluated in v1.
> **Assumption:** One OpenAI organisation key per deployment; users don't bring their own keys.
> **Assumption:** Files are stored on the VM's disk (Docker volume); object storage is a later change.
> **Assumption:** A single VM is sufficient for v1 load; no Kubernetes.
> **Assumption:** Tavily is acceptable as the web search provider.
> **Assumption:** Email verification and password reset by email are deferred (no email provider in v1); password reset is done by an admin CLI.

## 10. Open Questions

> **Open question:** Staging and production domains.
> **Open question:** Confirm OpenAI model names (`gpt-4.1`, `gpt-4.1-mini`, `text-embedding-3-small`) against the current lineup before launch.
> **Open question:** Is an admin UI needed in v1, or is CLI enough?
> **Open question:** Should registration be open, or invite-only on the hosted instance?

## 11. Out of Scope (v1)

Image generation and image/voice input; code execution sandbox; team workspaces and conversation sharing; custom assistants; plugin/MCP marketplace; mobile apps; multiple LLM providers; billing; OAuth/SSO; email notifications.

## 12. Timeline (relative, no fixed date)

| Milestone | Contents | Est. |
|-----------|----------|------|
| M0 Docs | Phase 1 docs, ADRs (this task) | week 0 |
| M1 Skeleton | Compose stack, auth, DB, React shell, CI | week 1–2 |
| M2 Core chat | Streaming chat, threads, tracing | week 3–4 |
| M3 Eval harness | Datasets, judge, CI gate | week 4 |
| M4 RAG | Upload, ingestion, citations | week 5–6 |
| M5 Tools + HITL | Web search approval, calculator | week 7 |
| M6 Memory | Extraction, memories UI | week 8 |
| M7 Hardening & launch | Limits, moderation, red-team, load test, deploy | week 9–10 |
