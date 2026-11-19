# Intake Questionnaire

> Fill this out before using any prompt in `docs/01_prompts/`.
> Your answers replace the `[[PLACEHOLDER]]` tokens in each prompt.
> Answers marked `(default)` were chosen as sensible defaults on 2026-11-19 and can be revised.

---

## 1. Project Basics

**1.1 Project name:**
`Nexus AI`

**1.2 One-sentence description:**
`A production-grade, self-hostable ChatGPT-style assistant with streaming chat, document Q&A, tool use with human approval, and long-term memory.`

**1.3 Team / company name:**
`Nexus AI Team` (default)

**1.4 Target launch date (or "no fixed date"):**
No fixed date (default)


---

## 2. Users & Problem

**2.1 Who are the primary users?**
(e.g. "Small business owners who need to track invoices")

Individual knowledge workers (developers, analysts, students) who want a private assistant that can answer questions about their own documents and remember their preferences, and engineers who self-host it as a reference AI application. (default)


**2.2 What problem does this solve?**

Hosted assistants are opaque: users cannot see or control what is remembered, cannot self-host with their own API key, and teams have no production-grade reference for building LLM apps with evals, tracing and guardrails. Nexus AI is a small, inspectable, self-hostable assistant built to production standards. (default)


**2.3 What does success look like in 6 months?**
(Key metrics, user counts, revenue, etc.)

- 100 weekly active users on the hosted instance (default)
- ≥ 70% of rated answers thumbs-up
- RAG faithfulness ≥ 0.85 and zero cross-user data leaks in evals and production
- p95 time-to-first-token < 2 s; average cost < $0.01 per message
- 99.5% monthly availability


---

## 3. Core Features

**3.1 List the 3–5 must-have features for v1:**
1. Core chat — streaming responses, conversation threads/history, auto-generated titles, stop, regenerate, markdown + code rendering
2. File upload + RAG — upload PDF/DOCX/TXT/MD, answers grounded in the user's files with citations
3. Tools / agent — web search, calculator, file search; human-in-the-loop approval for web search
4. Long-term memory — remembers user facts across conversations; user can view, edit, delete, or disable memory
5. Accounts & feedback — email/password accounts, thumbs up/down on answers, per-user usage limits

**3.2 List features explicitly OUT of scope for v1:**

Image generation and image/voice input, code execution sandbox, shared/team workspaces and conversation sharing, custom GPTs/assistants, plugins/MCP marketplace, mobile apps, multiple LLM providers, billing/payments, OAuth/SSO login. (default)


**3.3 Are there any existing systems this must integrate with?**
(APIs, databases, legacy services)

OpenAI API (chat, embeddings, moderation), Tavily search API (web search tool), Langfuse (self-hosted) for tracing, prompts, datasets and scores.


---

## 4. Tech Stack

**4.1 Backend language:**
`Python 3.12`

**4.2 Backend framework:**
`FastAPI` (with LangGraph for AI orchestration)

**4.3 Frontend framework:**
`React 18 + Vite + TypeScript`

**4.4 Database:**
`PostgreSQL 16` (plus Qdrant as vector DB — see §11)

**4.5 Cache / queue (if any):**
(e.g. Redis, RabbitMQ, none)

None for the app — background jobs use a Postgres-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`). (Langfuse brings its own Redis internally.) (default)

**4.6 Any preferred UI component library?**
(e.g. shadcn/ui, Tailwind, Material UI, none)

Tailwind CSS + shadcn/ui (default)

---

## 5. Design & Branding

**5.1 Primary brand color:**
`#0F766E` (deep teal) (default)

**5.2 Secondary / accent color:**
`#D97706` (amber accent) (default)

**5.3 Desired visual aesthetic:**
`minimal` — calm, content-first, generous whitespace (default)

**5.4 Heading font preference:**
`Inter` (default)

**5.5 Body font preference:**
`Inter` (default)

**5.6 Monospace font preference:**
`JetBrains Mono` (default)

**5.7 Font source:**
`self-hosted` via `@fontsource` npm packages (privacy, no third-party requests) (default)

**5.8 Dark mode requirement:**
`optional` — user toggle, defaults to system preference (default)

---

## 6. Authentication & Authorization

**6.1 Auth strategy:**
`JWT` — 15-min access token (Bearer) + 7-day rotating refresh token in httpOnly cookie (default)

**6.2 OAuth providers (if any):**
(e.g. Google, GitHub, none)

None in v1 (default)

**6.3 Authorization model:**
(e.g. RBAC with roles: admin/user, simple owner-based, none)

Owner-based: every resource belongs to one user; one `user` role in v1. Admin operations via CLI only. (default)

---

## 7. Deployment & Infrastructure

**7.1 Deployment target:**
`Docker Compose on a single Linux VM` (e.g. Hetzner/DigitalOcean) behind Caddy for TLS (default)

**7.2 Containerized?**
(Yes — Docker / Docker Compose / Kubernetes | No)

Yes — Docker + Docker Compose

**7.3 CI/CD preference:**
(e.g. GitHub Actions, GitLab CI, CircleCI, none)

GitHub Actions (default)

**7.4 Environments needed:**
(e.g. dev, staging, prod)

dev (local compose), staging, prod (default)

---

## 8. Scale & Non-Functional Requirements

**8.1 Expected users at launch:**

~100 registered, ~20 concurrent (default)


**8.2 Expected peak requests/second:**

~5 rps API, ~20 concurrent SSE streams (default)


**8.3 Data sensitivity:**
(e.g. PII, financial data, public data only)

PII — emails, conversation content, uploaded documents, extracted memories

**8.4 Compliance requirements:**
(e.g. GDPR, HIPAA, SOC2, none)

No formal certification; GDPR-aligned practices: account deletion erases all user data, data export, minimal retention (default)

**8.5 Uptime target:**
(e.g. 99.9%, best-effort)

99.5% monthly (default)

---

## 9. Testing Expectations

**9.1 Minimum unit test coverage target:**
(e.g. 80%, none for now)

Backend 80%, frontend 70% (default)

**9.2 E2e test tool preference:**
(e.g. Playwright, Cypress, none)

Playwright

**9.3 Contract testing:**
(Yes — generate from OpenAPI spec | No)

Yes — schemathesis against `docs/02_outputs/04_api_spec.yaml`

---

## 11. AI / LLM (skip if the product has no AI features)

**11.1 LLM provider:**
`OpenAI`

**11.2 Models and their roles:**
(e.g. "gpt-4.1 for chat, gpt-4.1-mini for titles/memory extraction, text-embedding-3-small for embeddings")

- Chat/agent: `gpt-4.1` (fallback `gpt-4.1-mini`)
- Titles, memory extraction, summarization, LLM-as-judge: `gpt-4.1-mini`
- Embeddings: `text-embedding-3-small` (1536 dims)
- Moderation: `omni-moderation-latest`
- All model names are env-configurable (default)


**11.3 Orchestration framework:**
`LangGraph` (Python)

**11.4 AI features in v1 (tick all that apply):**
- [x] Conversational chat (streaming, threads)
- [x] Retrieval-augmented generation (RAG) over user/company documents
- [x] Tool use / agents (web search, code exec, internal APIs)
- [x] Human-in-the-loop approval of agent actions
- [x] Long-term memory across conversations
- [ ] Structured extraction / classification
- [ ] Other:

**11.5 Knowledge sources for RAG:**
(e.g. user-uploaded PDFs/DOCX up to 20 MB, a docs site, a database; expected corpus size)

User-uploaded PDF, DOCX, TXT, MD; max 20 MB per file, 50 files per user; ~1–5k chunks per user (default)


**11.6 Vector database:**
`Qdrant`

**11.7 Latency budget:**
(e.g. time-to-first-token < 1.5 s p95, full answer < 15 s p95)

TTFT < 2 s p95 without tools; full answer < 20 s p95 (excluding time waiting for human approval) (default)


**11.8 Cost budget:**
(e.g. < $0.01 per message on average, per-user daily token cap of 200k)

< $0.01 per message average; 200k tokens per user per day; 30 messages per user per minute (default)


**11.9 Quality targets / eval expectations:**
(e.g. RAG faithfulness ≥ 0.85, tool-selection accuracy ≥ 0.9, "no regression vs. previous release")

RAG faithfulness ≥ 0.85, context recall ≥ 0.80, answer relevance ≥ 0.80, tool-selection accuracy ≥ 0.90, memory extraction precision ≥ 0.80, prompt-injection resistance ≥ 0.95, cross-user leakage = 0 (default)


**11.10 Safety & guardrails:**
(e.g. input moderation, prompt-injection defences, PII redaction, topics to refuse, age restrictions)

OpenAI moderation on input; prompt-injection defences for documents, web results and memories; PII redaction in traces; output length caps; tool argument validation; HITL approval for web search (default)


**11.11 LLM observability tool:**
`Langfuse` (self-hosted v3)

**11.12 Data retention for prompts/completions/traces:**
(e.g. traces kept 30 days, user can delete conversations and memories, no training on user data)

Conversations/files/memories kept until the user deletes them; account deletion erases everything (Postgres, Qdrant, files, memories) within 24 h; Langfuse traces kept 30 days; OpenAI API data is not used for training (default)


---

## 10. Open Questions

List any unknowns or decisions not yet made:

1. Staging and production domains (TBD)
2. OpenAI model lineup changes often — confirm chat/fast/embedding models before launch
3. Whether v1 needs an admin UI (currently CLI only)
