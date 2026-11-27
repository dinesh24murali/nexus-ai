# Memory Index

> Update this file whenever a memory file is created or significantly changed.
> AI tools: check this index before starting any task.

| File | Domain | Summary |
|------|--------|---------|
| `memory/project.md` | project | Stack, environments, key decisions, load and resilience test (`make loadtest`), open questions |
| `memory/ai.md` | ai | Models, prompt registry, orchestration graph (intent router and slash commands, dispatch, tools, approval interrupts, kill switches), background jobs, retrieval (ingestion, Qdrant layout), calculator and web search, deep research (plan card, approved-only searches, report, PDF), long-term memory (store, extraction job, filters, read side: lookup, selection, touch, memory by chat: list/save/forget), memory settings, budgets and moderation, eval harness and status (incl. `tools.routing`, `router.intents`, `memory.e2e`), trace ids, fake model failure markers (`[[fail:primary]]`, `[[fail:all]]`) |
| `memory/api.md` | api | API conventions: errors, pagination, SSE and chat runs (lease, cancel, regenerate, drain), 404 policy, rate limits, budgets, moderation, `/me/usage`, feedback, files (upload checks, storage, limits, delete), memories (CRUD, filters, 404/409/422 rules), tool approval (interrupt, resume, abandon, thread repair), routing (`route_selected`, slash commands, static replies, regenerate rule, `intent`/`route_source`), forget card (resume rules), deep research (`research_plan` card and resume rules, `progress`, streamed report), documents (pipeline events, the `/documents` API: list/get/delete/download, 404/409 rules, download headers and file-name sanitising) |
| `memory/auth.md` | auth | Invite-only registration, JWT + rotating refresh tokens, token families, owner-based authorization |

---

## How to Add an Entry

```
| `memory/<domain>.md` | <domain> | <one-line summary of what's in the file> |
```
