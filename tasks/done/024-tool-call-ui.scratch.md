# Scratch: 024-tool-call-ui

## Plan

1. Read `useChatRun.ts` events: add `toolCalls: LiveToolCall[]` to `ChatRun` (id, name, args, status `running | completed | failed | rejected | timed_out`, summary). `tool_start` adds a `running` entry (replaces one with the same id), `tool_end` settles the entry with the same id (adds one if the start was missed). Reset on a new run (`IDLE`).
2. `ToolCard.tsx`: one card per call. Label by tool and status ("Searching your files" / "Searched your files", "Searching the web" / "Searched the web", "Calculating" / "Calculated"; failed, rejected → "Declined", timed_out → "timed out"), the arguments as `name: value` pairs in the mono face (query or expression is the headline, other args are listed), the summary, a spinner for `running`. `role="group"`, `aria-label` = the label; a running card has `role="status"`. Unknown tool names (future tools) fall back to the raw name. Text only, long values wrap (`break-words`), no markup.
3. `ToolCalls` list component: renders the calls above the answer text of a message (stored `message.tool_calls`) and above the live text (`run.toolCalls`). While the live run owns the bubble, the stored message is not shown yet, so nothing shows twice (same rule as citations).
4. Component tests: every status, every tool, unknown tool, args as text (a `<b>` in a query stays text), long args, `running` spinner has an accessible name.
5. `MessageList.test.tsx` / `chat.test.tsx`: stored message with `tool_calls` renders cards; a live stream (`liveStream()`) with `tool_start` then `tool_end` shows running then completed.
6. Playwright `tools.spec.ts`: extend approve / reject / reload paths with the card assertions.
7. Docs: `frontend/AGENTS.md` (Tool cards section + structure line), `CHANGELOG.md`, `memory/api.md` nothing (no API change). Move task and scratch to `tasks/done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `frontend/src/features/chat/ToolCard.tsx` | create | `ToolCard`, `ToolCalls` |
| `frontend/src/features/chat/ToolCard.test.tsx` | create | every status and tool |
| `frontend/src/features/chat/useChatRun.ts` | modify | `toolCalls` in `ChatRun`, handle `tool_start`/`tool_end` |
| `frontend/src/features/chat/MessageList.tsx` | modify | render calls in stored and live bubbles |
| `frontend/src/features/chat/MessageList.test.tsx`, `chat.test.tsx` | modify | stored + live cases |
| `tests/e2e/tools.spec.ts` | modify | card assertions |
| `frontend/AGENTS.md`, `CHANGELOG.md` | modify | docs |

## AI Impact & Eval Plan

none. No prompt, model, tool, graph, retrieval or memory change; `make eval` not needed.

## Uncertainties

- Does a rejected call arrive as `tool_end` status `rejected` in the live stream? (TASK-023 notes say yes.) Verify in a test against the real stream events.
- The status colours: only design-token colours (`text-success-fg`? `text-warning-fg`?). Check `styles/tokens.css` for the names that exist before using them.

## What Will Be Skipped

- Collapsing / expanding results, showing raw tool output: the API only gives a short summary.
- A card while a call waits for approval (the `ApprovalCard` covers that).

## Risks

- Showing a card plus the stored message could duplicate calls when the run ends; mitigated by the same stored-id rule `MessageList` already uses for the answer text.
- `aria-live="polite"` on the log: a running card must not make screen readers repeat on every token. The card is static text apart from its status change.

## Ready to implement?

Yes.
