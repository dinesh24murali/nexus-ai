# Scratch: 027-memories-api-ui

## Plan

1. **Spec first**: `listMemories` gets the `400` (bad cursor) response like the other lists; everything else already matches. `gen:api` regenerates `frontend/src/api/schema.ts`.
2. **Service** (`app/services/memories.py`, `MemoryService(settings, factory, get_embedder)`): one store per call (own token counter, lazily built embedder so list/delete need no API key); `list_page(user, limit, cursor)` (newest first by created_at then id, cursor = the existing `encode_cursor`), `create` (normalise + `policy.check` -> 422 `VALIDATION_FAILED` with a fixed message and no text; near-duplicate >= 0.9 -> 409 `MEMORY_DUPLICATE`; `source=manual`, category `fact`; enforce the 200 cap), `update` (same filter, 404 when not the caller's), `delete` (404), `clear` (idempotent 204). Embedding spend of create/update recorded in `usage_daily` (`requests=0`) in a `finally`. Works with memory off (the user can still view and delete).
3. **Router** `app/api/memories.py` (thin) + schemas (`MemoryOut`, `MemoryPage`, `MemoryInput` mirroring the spec: 1..500, same nullability), `MemoriesDep`, registered in `main.py`.
4. **Tests**: integration over HTTP (CRUD, pagination, isolation 404 for A's id used by B, filters 422 for sensitive/instruction text and the body never echoed, duplicate 409, cap/eviction, memory-off still lists, clear all only own), unit for cursor/validation; `tests/contract` gets the endpoints (schemathesis picks them up from the spec; check it runs).
5. **Frontend**: `features/settings/memories.ts` (TanStack hooks with user-id keys, `useMemories`, `useCreate/Update/Delete/ClearMemories`, `useMemoryEnabled` using `PATCH /me` and a new `AuthManager.setUser`), `features/settings/Memory.tsx` (page: switch "Use long-term memory", add form, list with inline edit and delete confirm, "Clear all" with confirm, empty/error/loading states, source label), route `/settings/memory`, sidebar link. **Toast**: `components/Toasts.tsx` (provider + `role="status"` live region, auto-dismiss, link "Manage") mounted in `AppShell`; `features/settings/useMemoryToast.ts`: when a run completes with memory on, snapshot the memory ids/`updated_at` before the send, re-read the list at 2, 5, 10 and 20 s (stops at the first change or on unmount) and toast "Nexus remembered N thing(s). Manage" when something was created. (The extraction job runs after the stream has ended, so the stream cannot carry it; the `memory_updated` stream event stays for `manage_memory`.)
6. **Tests**: component tests (page states, inline edit, delete confirm, clear all, toggle updates the user, toast appears and goes), `renderApp` pages tests for the route; e2e `tests/e2e/memory.spec.ts` (fake LLM, worker running): send "Remember that I prefer Go.", toast, memory listed, edit, delete, toggle off, clear all.
7. **Docs/gates**: `memory/api.md` (endpoints), `frontend/AGENTS.md` (Memory section + structure), `backend/AGENTS.md` (structure), `tests/coverage-baseline.json`, `CHANGELOG.md`, doc 10 note (manual writes filtered); move task files to `tasks/done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `docs/02_outputs/04_api_spec.yaml` | modify | 400 on list |
| `backend/app/services/memories.py`, `app/api/memories.py` | create | service, router |
| `backend/app/api/schemas.py`, `app/api/deps.py`, `app/main.py` | modify | schemas, dep, wiring |
| `backend/tests/api/test_memories.py`, `tests/services/test_memories.py` | create | tests |
| `frontend/src/api/schema.ts` | regenerate | |
| `frontend/src/features/settings/{memories.ts,Memory.tsx,useMemoryToast.ts,*.test.tsx}` | create | page, hooks |
| `frontend/src/components/Toasts.tsx` (+ test) | create | toast |
| `frontend/src/lib/auth.ts`, `routes/AppShell.tsx`, `App.tsx`, `features/chat/useChatRun.ts` (or `ChatView`) | modify | setUser, link, route, toast hook |
| `tests/e2e/memory.spec.ts` | create | |
| docs, memory files, `tests/coverage-baseline.json`, `CHANGELOG.md` | modify | |

## AI Impact & Eval Plan

none (no prompt, model, tool or retrieval change). Manual writes use the extraction filters, covered by the API tests.

## Uncertainties

- Cursor for the store-backed list: the store has no keyset listing, but the cap is 200, so the service loads the user's memories and slices after the cursor (documented, cheap).
- Toast timing is polling-based (the job is asynchronous); the delays are parameters so tests run scaled down.
- Whether schemathesis can fuzz these endpoints against the store without an OpenAI key: embeddings run in fake mode in the test app (`LLM_MODE=fake`), as for the job tests.

## What Will Be Skipped

- `memory_updated` stream events (belong to `manage_memory`, M7), editing the category, rate limiting of memory writes (bounded by the 200 cap), data export.

## Risks

- A wrong user id in the service would expose memories: every call takes the trusted `current.id`; isolation tests for each endpoint.
- Polling for the toast adds a few requests after a turn: only when memory is on, 4 reads at most, stops at the first change.

## Ready to implement?

Yes.
