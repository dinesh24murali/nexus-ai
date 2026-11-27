# Task: 024-tool-call-ui

**Status:** done
**Created:** 2026-11-21
**ADR refs:** ADR-0006 (stop = cancel, stream is a view of the run)

---

## Goal

Show what Nexus does with its tools: a card per tool call with its name, arguments and status, live while the run streams and again from the stored `tool_calls` when the history is read.

## Background

TASK-024 in `docs/02_outputs/05_implementation_plan.md` (M4). The API already sends `tool_start` / `tool_end` events and stores `tool_calls` on the assistant message (TASK-019, 022, 023); the frontend ignored both (the stream client already types them). Functional spec FS-005: "tool card shows name, args, spinner → result summary", a timeout shows "Search timed out", a rejected search shows that it was declined.

## Acceptance Criteria

- [x] Component tests for every status (running, completed, failed, rejected, timed out) and for every tool name
- [x] Live: `tool_start` shows a running card, `tool_end` settles it; stored `tool_calls` render in the history after a reload
- [x] Tool arguments and summaries are rendered as text only (model-controlled data)
- [x] Playwright (`tools.spec.ts`, fake LLM): the search card shows the query and "completed" after an approval, "rejected" after a reject, and survives a reload
- [x] `make verify` passes; coverage gate for `frontend/src/features/chat` not lowered

## Dependencies

- **Tasks:** TASK-013 (chat UI), TASK-022/023 (tools, approval)
- **Memory files:** `memory/api.md`, `memory/ai.md` (Tools)
- **Docs:** functional spec FS-005, design system 07 (tool args use the mono face, 14 px)

## AI Impact

none (frontend only; no prompt, model, tool or graph change)

## Files Expected to Change

See `024-tool-call-ui.scratch.md`.

## Notes

- No API change: `ToolCall`, `ToolStartEvent` and `ToolEndEvent` are already in the spec.
- Approval cards (TASK-023) stay as they are; a tool card for a call that is still waiting for approval does not exist (the call has not started).
