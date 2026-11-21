# Memory Index

> Update this file whenever a memory file is created or significantly changed.
> AI tools: check this index before starting any task.

| File | Domain | Summary |
|------|--------|---------|
| `memory/project.md` | project | Stack, environments, key decisions, open questions |
| `memory/ai.md` | ai | Models, prompt registry, orchestration graph, background jobs, retrieval (ingestion, Qdrant layout) & memory settings, budgets and moderation, eval harness and status, trace ids |
| `memory/api.md` | api | API conventions: errors, pagination, SSE and chat runs (lease, cancel, regenerate, drain), 404 policy, rate limits, budgets, moderation, `/me/usage`, feedback, files (upload checks, storage, limits, delete) |
| `memory/auth.md` | auth | Invite-only registration, JWT + rotating refresh tokens, token families, owner-based authorization |

---

## How to Add an Entry

```
| `memory/<domain>.md` | <domain> | <one-line summary of what's in the file> |
```
