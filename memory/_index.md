# Memory Index

> Update this file whenever a memory file is created or significantly changed.
> AI tools: check this index before starting any task.

| File | Domain | Summary |
|------|--------|---------|
| `memory/project.md` | project | Stack, environments, key decisions, load and resilience test (`make loadtest`), open questions |
| `memory/ai.md` | ai | Models, prompt registry, orchestration graph (tools, approval interrupts, kill switch), background jobs, retrieval (ingestion, Qdrant layout), calculator and web search, long-term memory (store, extraction job, filters, read side: lookup, selection, touch), memory settings, budgets and moderation, eval harness and status (incl. `tools.routing`), trace ids, fake model failure markers (`[[fail:primary]]`, `[[fail:all]]`) |
| `memory/api.md` | api | API conventions: errors, pagination, SSE and chat runs (lease, cancel, regenerate, drain), 404 policy, rate limits, budgets, moderation, `/me/usage`, feedback, files (upload checks, storage, limits, delete), memories (CRUD, filters, 404/409/422 rules), tool approval (interrupt, resume, abandon, thread repair) |
| `memory/auth.md` | auth | Invite-only registration, JWT + rotating refresh tokens, token families, owner-based authorization |

---

## How to Add an Entry

```
| `memory/<domain>.md` | <domain> | <one-line summary of what's in the file> |
```
