# Task: 040-mode-chip-slash-menu

**Status:** done (the document card half is deferred as D33)
**Created:** 2026-11-22
**ADR refs:** ADR-0010 (intent router), ADR-0006 (SSE)

---

## Goal

Show the user which pipeline answered (a mode chip), let them type slash commands from a `/` menu, and stop offering Regenerate on answers that cannot be regenerated.

## Background

Plan v1.3, TASK-040 (M7, `[011-intent-router]`). TASK-038 built the router, the slash-command parser and `route_selected`; the UI still ignores the event and still shows Regenerate on memory / `/help` answers, which the API refuses with 409 `REGENERATE_NOT_SUPPORTED`. Doc 08 section 2.6: "The composer's `/` menu only inserts the command text" (the backend parses it).

## Acceptance Criteria

- [x] Component tests: `ModeChip`, `SlashMenu` (open, filter, keyboard, insert, Escape), `MessageList` (chip on stored and live answers; no Regenerate on non-chat answers), `useChatRun` (route captured from `route_selected`)
- [x] Playwright (fake LLM): a `/pdf` message shows a Document chip and Regenerate is hidden on it. **The document card half of the plan's criterion is TASK-042's (`DocumentCard`); deferred as D33**
- [x] `route_source` is part of the `Message` schema and the generated client, so the UI applies the same regenerate rule as the API

## Dependencies

- **Tasks:** 038 (done), 039 (done); the Playwright case needs 041 (`/pdf` must do something)
- **Memory files:** `memory/api.md` (routing section), `memory/ai.md` (slash commands), `frontend/AGENTS.md`
- **Docs:** doc 08 sections 2.2, 2.6, 8; `docs/02_outputs/07_design_system.md` (tokens only)

## AI Impact

none (no prompt, model, graph or retrieval change; the spec gains one read-only field)

## Files Expected to Change

See `040-mode-chip-slash-menu.scratch.md`.

## Notes

- Regenerate rule (backend `runs.py::_regenerable`): allowed only when `intent` is null or `general_chat`, and not when `intent` is null with `route_source = 'command'` (a `/help` or usage reply).
