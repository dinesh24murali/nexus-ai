# Scratch: 040-mode-chip-slash-menu

> Written before any code (AGENTS.md 3.1). Frontend task; one additive spec field. TASK-041 has its own scratch file.

## Plan

1. **Spec + client:** add nullable `route_source` (`router | command | fallback`) to `Message` in `docs/02_outputs/04_api_spec.yaml` (bump the spec version), expose it in `api/schemas.py::MessageOut`, run `npm run gen:api`. Reason: the UI must not guess which answers are static (`/help`, usage replies: `intent` null, `route_source` `command`).
2. **`features/chat/modes.ts`** (pure): `modeOf(intent)` -> `{label, tone}` for `manage_memory` ("Memory"), `generate_document` ("Document"), `deep_research` ("Research"); `general_chat`/null -> no chip. `canRegenerate(message)` mirrors the backend rule. `COMMANDS` list for the menu (name, insert text, hint) from doc 08 section 2.6, and `matchCommands(draft)`.
3. **`ModeChip.tsx`:** small text chip (design tokens only) with an accessible name ("Answered by: Document"), `title` says whether it was chosen by a command or automatically.
4. **`useChatRun`:** keep `route: {intent, source} | null` from `route_selected` in the run state so the live bubble shows the chip before the stored message arrives; reset per run.
5. **`MessageList`:** chip above the stored answer (from `message.intent`) and above the live answer (from `run.route`); Regenerate only when `canRegenerate(message)`; the live bubble never has one.
6. **`SlashMenu.tsx` + `Composer`:** the menu opens while the draft is a single `/word` at the start (no space yet); lists matching commands; ArrowUp/Down moves, Enter/Tab inserts the command text (not sending), Escape closes; click inserts. ARIA combobox pattern on the textarea (`role=combobox`, `aria-expanded`, `aria-controls`, `aria-activedescendant`, listbox + options). Inserting only sets the draft text (`/pdf `, `/memory remember `...); the backend parses.
7. **Tests:** `modes.test.ts`, `ModeChip.test.tsx`, `SlashMenu.test.tsx` (inside `Composer`), additions to `MessageList.test.tsx` and `chat.test.tsx` (route event -> chip; regenerate hidden), `stream.test.ts` untouched (event already known).
8. **Playwright** `tests/e2e/commands.spec.ts` (fake LLM): `/pdf` -> Document chip, no Regenerate; `/help` -> no chip and no Regenerate; `/` menu inserts text. Needs TASK-041's backend and a rebuilt stack.
9. **Docs:** `frontend/AGENTS.md` (Chat modes section), `memory/api.md` (`route_source` on Message), CHANGELOG, plan checkboxes, `tasks/deferred.md` D33, move task files to `done/`.

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `docs/02_outputs/04_api_spec.yaml` | modify | `Message.route_source`, version bump |
| `backend/app/api/schemas.py` | modify | `MessageOut.route_source` |
| `backend/tests/api/test_chat_routing.py` (or `test_schemas.py`) | modify | the field is returned |
| `frontend/src/api/schema.ts` | regenerate | `npm run gen:api` |
| `frontend/src/features/chat/modes.ts` (+ `.test.ts`) | create | labels, regenerate rule, command list |
| `frontend/src/features/chat/ModeChip.tsx` (+ test) | create | |
| `frontend/src/features/chat/SlashMenu.tsx` (+ test) | create | |
| `frontend/src/features/chat/Composer.tsx` | modify | menu wiring, keyboard handling |
| `frontend/src/features/chat/useChatRun.ts` | modify | `route` in run state |
| `frontend/src/features/chat/MessageList.tsx` (+ test) | modify | chip, regenerate rule |
| `frontend/src/features/chat/chat.test.tsx` | modify | live chip |
| `tests/e2e/commands.spec.ts` | create | Playwright |
| `frontend/AGENTS.md`, `memory/api.md`, `CHANGELOG.md`, `docs/02_outputs/05_implementation_plan.md`, `tasks/deferred.md` | modify | |

## AI Impact & Eval Plan

none. No prompt, model, tool, graph or retrieval change; no eval run needed.

## Uncertainties

- Whether `general_chat` answers get a chip: decided no (a chip on every answer is noise); only the pipelines that differ from plain chat do.
- Fallback: a router failure shows no chip (the answer is a chat answer).
- The Playwright `/pdf` case needs TASK-041 in the running stack (`make up` rebuilds; `LLM_MODE=fake`).

## What Will Be Skipped

- **The document card** (`DocumentCard.tsx`, polling, download) is TASK-042; the criterion's "document card" half is deferred as D33. The chip and the hidden Regenerate are done here.
- Rendering `progress` and `document_created` events: known to the stream client, not rendered (TASK-042).
- A `/` button in the composer (typing `/` is enough for v1.1).

## Risks

- The slash menu must not swallow Enter when it is closed, and must not open for a message that merely contains `/` later (`and/or`) or for pasted paths (`/etc/hosts`): it opens only while the draft is exactly one `/word` token at the start. A path such as `/etc/hosts` has no matching command, so the menu stays closed and Enter sends.
- IME composition: Enter while composing must not select a menu item (same guard as the existing submit).
