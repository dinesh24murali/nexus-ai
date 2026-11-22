# Task: 027-memories-api-ui

**Status:** done
**Created:** 2026-11-22
**ADR refs:** ADR-0003, ADR-0009

---

## Goal

The user sees and controls what Nexus remembers: CRUD endpoints on their memories, and a Settings -> Memory page with the on/off switch, inline edit, delete, clear all, and a "Memory updated" toast after a turn that stored something.

## Background

TASK-027 in `docs/02_outputs/05_implementation_plan.md` (M5); functional spec UF-006 and FS-006; the API contract (`/memories`, `/memories/{memoryId}`, `Memory`, `MemoryInput`) is already in `docs/02_outputs/04_api_spec.yaml`. The store and filters come from TASK-025, reading from TASK-026. Route `/settings/memory` (functional spec).

## Acceptance Criteria

- [x] Playwright `memory.spec.ts` passes (toggle, edit, delete, clear all, toast after "Remember that I prefer Go")
- [x] `GET/POST/DELETE /memories`, `PATCH/DELETE /memories/{id}` behave as the spec says; another user's id is 404; text goes through the same filters as extraction (422 without echoing the text); a near-duplicate is 409
- [x] Contract tests cover the new endpoints; the spec is updated first for anything stricter than it says
- [x] Component tests for the page and the toast; coverage gates hold

## Dependencies

- **Tasks:** TASK-025, TASK-026, TASK-007
- **Memory files:** `memory/api.md`, `memory/ai.md`, `memory/auth.md`
- **Docs:** functional spec UF-006 / FS-006, API spec

## AI Impact

- **Prompts touched:** none
- **Models / params changed:** none (a manual create/edit embeds its text; spend recorded with `requests=0`)
- **Graph / tools / retrieval changes:** none
- **Eval datasets affected / added:** none
- **Threat model entries:** LLM-T04 (manual writes use the same filter)
- **Expected cost / latency delta:** one embedding per manual create/edit

## Files Expected to Change

See `027-memories-api-ui.scratch.md`.

## Notes

- Memory text is user data: never logged. Frontend renders it as text only.
