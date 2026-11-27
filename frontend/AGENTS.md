# frontend/ — AGENTS.md

## Stack

- **Framework:** `React 19 + Vite + TypeScript 5.9`
- **UI library:** `Tailwind CSS 4 (CSS-first @theme) + shadcn/ui`
- **API client:** generated from `docs/02_outputs/04_api_spec.yaml`

---

## Project Structure

```
frontend/
├── src/
│   ├── main.tsx, App.tsx         ← entry (providers) and route table
│   ├── api/schema.ts             ← GENERATED from the OpenAPI spec (`npm run gen:api`); never edit
│   ├── api/client.ts             ← typed openapi-fetch client on top of AuthManager.fetch; `useApiClient()`, `unwrap()`
│   ├── api/stream.ts             ← the ONE hand-written client: SSE parser + `openStream()` (types from the schema)
│   ├── lib/                      ← auth.ts (session manager), auth-context.ts, AuthProvider.tsx,
│   │                               theme.ts, messages.ts (error text), cn.ts
│   ├── routes/                   ← pages (LoginPage, RegisterPage, AppShell, ...) and guards.tsx
│   ├── components/ui/            ← shadcn-style primitives (Button, Field, Alert) on our tokens
│   ├── components/               ← app components (ThemeToggle, Toasts + toast-context, sidebar/: ConversationNav, ConversationList, ConversationItem)
│   ├── features/                 ← feature folders (conversations/ = TanStack Query hooks; chat/ = ChatView, useChatRun,
│   │                               MessageList, Composer, Feedback (thumbs), Markdown + CodeBlock + highlight (Shiki), notices, UsageMeter,
│   │                               ApprovalCard (web-search approve / edit / reject; routes to ResearchPlanCard and the forget card) + approvals.ts (shared types, `useExpired`);
│   │                               ModeChip + modes.ts (which pipeline answered, the Regenerate rule, the command list),
│   │                               SlashMenu (the `/` menu in the Composer);
│   │                               ToolCard + toolCalls.ts (tool call cards, live and stored);
│   │                               documents/ = DocumentCard (under the answer that made a PDF), DocumentsPage, api.ts hooks
│   │                               (list, one, delete, download), messages.ts, timing.ts (poll interval);
│   │                               Citations (chips + snippet popover); files/ = FilesPage, uploads, chips, paperclip;
│   │                               settings/ = Memory (Settings → Memory page), memories.ts hooks, useMemoryWatch (toast))
│   ├── styles/tokens.css, theme.css   ← design tokens + Tailwind 4 @theme (docs/02_outputs/07)
│   └── config.ts                 ← build-time VITE_* config
├── public/theme-init.js          ← sets data-theme before first paint (CSP forbids inline scripts)
└── Dockerfile                    ← builds the SPA into the Caddy image (context = repo root)
```

---

## Conventions

### API Client
- The API client in `src/api/` (or equivalent) is **generated from** `docs/02_outputs/04_api_spec.yaml`
- Do not manually write API fetch calls that duplicate the spec
- To regenerate: `cd frontend && npm run gen:api` (fill in your actual codegen command)
- **Streaming exception:** streaming endpoints (SSE/WebSocket) use one hand-written stream client (e.g. `src/api/stream.ts`); its event types must come from the generated schema types — see `AGENTS.md` §3.7
- Render model output as untrusted: sanitize markdown/HTML, no `dangerouslySetInnerHTML` on raw model text, external links get `rel="noopener noreferrer"`

### Chat (TASK-013)
- `api/stream.ts` reads SSE from a `fetch` body (an `EventSource` cannot POST or send a bearer token). `SseParser` is chunk-size independent; a stream that ends without `done`/`error`/`interrupt` throws `StreamInterrupted`; unknown event types are ignored. A refusal before the stream opens (429, 400, 409, 503) is the usual `ApiProblem`
- **Stop = `POST …/runs/cancel`**, never `abort()`: aborting only closes the connection and the run keeps going server-side (ADR-0006). Leaving the page aborts the connection and nothing else. "Connection lost" → Reload re-reads the history; it never resends
- `useChatRun` owns one conversation's run. The live bubble yields to the stored message the moment its id (from `run_started`) is in the history, so nothing shows twice; the regenerated answer is hidden by id while its replacement streams
- Model output is untrusted: `Markdown.tsx` has no `rehype-raw`, drops `javascript:` URLs, opens links with `rel="noopener noreferrer"`, never fetches images. Code colours: Shiki tokens become classes (`highlight.ts`), never `style` (the CSP forbids it); only design-token colours are used
- The draft text lives in the page (`Composer` is controlled) so a send refused after the home-page hand-off can put it back
- Ratings: `Feedback.tsx` (thumbs under a complete or stopped answer, not a failed or empty one) + `feedback.ts` (`useRate`: the rating shows at once, goes back with a message if the server refuses). The API cannot withdraw a rating, so pressing the chosen thumb again sends nothing. Anything that renders `MessageList` needs the query and auth providers
- Tests: `renderApp(path, handler, {strict: true})` runs under StrictMode; `liveStream()` feeds a stream event by event; use `seen(text)` rather than `expect(await findByText(..)).toBeInTheDocument()` for text that moves from the live bubble to the stored message. `tests/e2e/chat.spec.ts` needs the stack in `LLM_MODE=fake` with `LLM_FAKE_TOKEN_DELAY_S=0.05` and fails loudly otherwise

### Modes and slash commands (TASK-040)
- `route_selected` is kept in `useChatRun` state (`route`) so the live answer shows its chip before the stored message arrives; the stored message has `intent` and `route_source`. Only the pipelines that differ from chat get a chip (`modes.ts::modeLabel`: Memory, Document, Research); a plain chat answer has none. The chip is text with an `sr-only` "Answered by:" prefix
- **Regenerate follows the API's rule** (`modes.ts::canRegenerate`, the same as `runs.py::_regenerable`): only an answer with no intent or `general_chat`, and not a static reply (`intent` null with `route_source` `command`: `/help`, usage messages). A failed chat answer still gets Retry. Change both together
- The `/` menu (`SlashMenu`, driven from `Composer`) opens only while the draft is ONE `/word` at the start, so `and/or` and `/etc/hosts` never open it; it only inserts text (the backend parses the command). The textarea keeps focus and points at the highlighted row with `aria-activedescendant`; Enter completes the highlighted command unless the box already holds exactly that command (then it sends), Tab completes, Escape closes until the draft changes, and nothing opens while a run is live. The list is `modes.ts::COMMANDS` (doc 08 section 2.6): a new command is added there
- `progress` and `document_created` are handled: see Research plan and Documents below
- `tests/e2e/commands.spec.ts` needs `LLM_MODE=fake` on api AND worker

### Research plan (TASK-043)
- `ResearchPlanCard` (`ApprovalCard` routes `tool: 'research_plan'` to it): the topic and the exact queries as text, **Approve and search** / **Edit** / **Don't search**. Editing is a form of one text field per query (at most 6, at least 1, each <= 400 characters, add and remove, blanks are dropped); unchanged queries send a plain approve, changed ones `{decision: 'edit', queries}` which `useChatRun.decide` posts as `edited_args.queries` (the API refuses anything else with 422 and the card stays). Nothing is searched before the answer and nothing else will be: the card says so. An expired plan shows "expired" and no buttons; a plan it cannot read can only be rejected. Like every approval it is read from the conversation (`pending_interrupt`), so it survives a reload
- `Decision` / `PendingInterrupt` / `useExpired` live in `approvals.ts` (the cards import them there; `ApprovalCard.tsx` re-exports the types)
- `useChatRun.progress` keeps the last `progress` event and `modes.ts::progressText` turns it into "Planning the research…", "Searching the web: 2 of 4 done…", "Writing the report…", "Preparing the PDF…" where "Thinking…" would be, until the answer starts streaming. The searches are ordinary tool cards, the report an ordinary answer with citation chips and (for a PDF) the document card; the mode chip says Research and Regenerate is hidden (`canRegenerate`)
- `tests/e2e/research.spec.ts` needs `LLM_MODE=fake` on api AND worker (planner, report and web search are scripted offline; the PDF is made by the real renderer). In the composer the `/` menu completes `/research` first: a bare `/research` needs a second Enter to send

### Documents (TASK-042)
- `features/documents/`: `api.ts` (`useDocuments` polls every 2 s only while one is `pending`; `useDocument(initial)` seeds a card from the message and polls until the render ends, a 404 is not retried and means "deleted"; `useDeleteDocument`; `useDownloadDocument`), `DocumentCard` (+ `DocumentCards`), `DocumentsPage` (`/documents`: list, status, Download, inline-confirm delete, link to the chat; sidebar link), `messages.ts` (kind labels, size, `fileNameFor`), `timing.ts` (`POLL_MS`, shrunk in tests with `vi.mock('./timing')`)
- The card is under the answer, live from `document_created` (`useChatRun.documents`; it yields to the stored message with the text, and the stored `message.documents` use the same query key, so it never shows twice and never flickers) and stored from `Message.documents`, which is only a snapshot: the card re-reads `GET /documents/{id}` while pending. The title is model-written: it is rendered as text only
- **Download = `manager.fetch` (Bearer, one refresh retry) -> `Blob` -> `URL.createObjectURL` -> a hidden `<a download>` click -> revoke.** No token in a URL, no new CSP entry (a `blob:` download is not a fetch: `tests/e2e/documents.spec.ts` proves it in the real browser). The file name is `fileNameFor(title)` (the server sends the same kind of name in its header)
- A card has no `role="status"` (pages keep one `status`: the running tool call); the progress text is plain text inside the `role="log"` region. Tests: jsdom has no object URLs, so `documents.test.tsx` stubs `URL.createObjectURL` and the anchor's `click`; read a jsdom `Blob` with `FileReader`, not `Response`
- `tests/e2e/documents.spec.ts` needs `LLM_MODE=fake` on api AND worker (the real renderer makes the PDF); on a dev stack that has already spent today's fake budget the global cap answers 429 `BUDGET_EXCEEDED`: raise `GLOBAL_DAILY_BUDGET_USD` for the run (as `loadtest/compose.loadtest.yml` does)

### Tool approval (TASK-023)
- The approval is read from the conversation (`pending_interrupt`), not kept in run state: a run that ends with an `interrupt` event re-reads the conversation, which is also how a reload restores the card. The card shows only while no run is active; `useChatRun.decide` posts to `/runs/resume` and the resumed run streams like any other
- A run that is refused before it starts (409 `NO_PENDING_INTERRUPT`) re-reads the conversation as well, so a stale card disappears
- `ApprovalCard` renders the query as text and limits an edit to 400 characters, the API's limit; an expired approval shows "expired" and no buttons

### Tool cards (TASK-024)
- `ToolCard` shows one call: what it did in words (`toolCalls.ts::labelOf`: "Searching the web" → "Searched the web", "Web search declined", "… timed out"), the query or sum as the headline, other arguments as `key: value` in the mono face, and the tool's `summary`. Only a running call is `role="status"` (spinner); everything is text, never markup. A tool name this build does not know shows as `name: done` with all its arguments, so a new backend tool never breaks the page
- Live: `useChatRun` keeps `toolCalls` from `tool_start` (running) and `tool_end` (settled; a missed start still shows, with no arguments). The live calls sit in the live bubble and yield to the stored message's `tool_calls` at the same moment as the answer text, so a call never shows twice. A call that is waiting for approval has no card (`ApprovalCard` covers it), and calls of the part of a turn before an approval show again only in the stored answer
- A declined call has no `tool_start` (it never started), so its card has no query; a new tool needs a row in `toolCalls.ts::KNOWN` for its words and headline argument

### Files (TASK-021)
- `features/files/`: `api.ts` (`useFiles` polls every 2 s only while a file is `pending`/`processing`; `useUploadFile`, `useDeleteFile`), `uploads.ts` (`useAttachments`: at most 5 files at once, one chip per upload), `FilesPage` (`/files`: dropzone, table, `n/50`, inline delete confirm), `AttachButton` (`FilePicker`, the paperclip and the page's button), `UploadChips` (Uploading… → Processing… → Ready / Failed), `messages.ts` (texts for the file error codes). The composer takes the paperclip through its optional `attach` prop; chips are rendered by the page above it
- **No byte progress**: `fetch` cannot report upload bytes and every call must go through `manager.fetch` (token refresh), so the chip is a state, not a percentage
- The openapi-fetch adapter sends a `multipart/form-data` body as an `ArrayBuffer` (`text()` would corrupt binary files). Build the body with `bodySerializer` returning a `FormData`
- **Errors from `unwrap` / `unwrapEmpty` keep the API error code** (`problemFromBody`): openapi-fetch has already read the body, so `parseProblem(response)` on it would turn every code into `HTTP_ERROR`. Use `unwrapEmpty` for 204 calls
- Citation chips (`features/chat/Citations.tsx`) render under stored assistant messages from `message.citations`, as plain text; web links only for `http(s)` URLs. Live `citation` events are not shown while streaming (the chips appear when the stored message replaces the live bubble)
- Tests: in jsdom a `File` cannot be read by Node's fetch (it hangs), so tests build uploads with `File` from `node:buffer` (see `files.test.tsx`); the bytes on the wire are proven by `tests/e2e/rag.spec.ts`. `tests/e2e/rag.spec.ts` needs `LLM_MODE=fake` on api AND worker; the fake embeddings are a hashed bag of words, so keep the fixture file short (a one-word query must stay above the 0.25 score cut-off)

### Memory (TASK-027)
- `/settings/memory` (`features/settings/Memory.tsx`): the switch (`PATCH /me`, then `AuthManager.updateUser` so every screen sees `memory_enabled`), add, inline edit, delete with an inline confirm, clear all with a count. Keys are `['memories', uid, 'list']`; the list is read whole (a user has at most 200: two pages of 100). Memory text is rendered as text, never as markup. Managing works with the switch off
- **Toast after a turn** (`useMemoryWatch`, called from `useChatRun.start` for a send and for the resume that ends a turn, not for a regenerate): the extraction job runs after the stream ends, so the stream cannot say what was stored. `begin()` reads the memory list when a turn starts (the cache is reused for 30 s); `finish()` re-reads it at 1.5, 4, 9 and 20 s and shows "Nexus remembered N thing(s). Manage" for new or changed memories, then stops. Memory off, a failed read, or a failed run means no checks and no toast. Delays live in `features/settings/timing.ts` so tests shrink them (`vi.mock('../settings/timing', ...)`)
- Toasts: `ToastProvider` (in `AppShell`) renders a labelled `aria-live="polite"` region; a toast is a list item, not a `status` role (pages keep exactly one `status`: the running tool call). `useToasts()` is a no-op outside a provider
- `tests/e2e/memory.spec.ts` needs `LLM_MODE=fake` on api AND worker (the fake extraction turns "Remember that I prefer Go for backend work." into "User prefers Go for backend work")

### Forget card (TASK-039)
- `ApprovalCard` shows the card for the pending approval's tool: `web_search` (query, edit), `forget_memory` (`ForgetMemoryCard`: the exact text of each memory the server found, as text; one candidate → "Forget it" / "Keep it"; several → a radio group with nothing preselected, "Forget the selected memory" stays disabled until one is chosen, "Keep them all"), anything else → the "can't be shown here yet" card with Reject. Candidates are read defensively (anything that is not `{memory_id: string, content: string}` is dropped; a card with none can only be kept)
- The decision type carries the choice: `{decision: 'approve', memoryId}`; `useChatRun.decide` posts it as `edited_args.memory_id` (the API refuses an id that was not offered, 422, and the card stays). An expired card shows "expired" and no buttons. Like every approval it is read from the conversation (`pending_interrupt`), so it survives a reload, and a new message abandons it
- `route_selected` and `memory_updated` events are known to `api/stream.ts` but not rendered yet (the mode chip and slash menu are TASK-040); a memory answer is an ordinary assistant message, so the toast after a turn (`useMemoryWatch`) still tells the user a memory was saved
- `tests/e2e/memory.spec.ts` (memory by chat: `/memory remember`, `/memory`, `/help`, `/pdf` explained, forget keep / forget / reload / abandon) needs `LLM_MODE=fake` like the rest of that file; plain-text requests are never routed in fake mode

### Auth and sessions
- All session logic is `src/lib/auth.ts` (`AuthManager`). Use `manager.fetch()` for every API call: it adds the bearer token, refreshes once on 401 and retries. Never put tokens in localStorage, sessionStorage, URLs or the DOM
- **Refresh must never overlap.** Refreshing rotates the refresh cookie and the server revokes the whole session if an old token is reused. The manager keeps one refresh in flight per tab and one at a time across tabs (Web Locks). Do not call `/auth/refresh` yourself
- Tokens are only sent to our own origin

### Component Structure
- Pages in `src/routes/`, feature code in `src/features/<area>/`, primitives in `src/components/ui/`
- Co-locate tests: `Thing.test.tsx` next to `Thing.tsx`. Page tests render the real `<App />` inside a `MemoryRouter` with only `fetch` scripted (`src/test-utils.tsx`)
- Named exports (the default export is only `App`). No inline `style` attributes: the CSP forbids them (use Tailwind utilities and tokens)
- Every form control has a visible label; errors use `role="alert"`/`aria-invalid`/`aria-describedby` (see `Field`)

### Styling
- Tokens only (`bg-surface`, `text-fg-muted`, `text-h3`, `rounded-lg`, ...). Do not introduce colors or fonts outside `docs/02_outputs/07_design_system.md`
- Theme: `data-theme` on `<html>`, set by `public/theme-init.js` before paint and by `lib/theme.ts`. Keep both in sync (storage key `nexus-theme`)
- Fonts are self-hosted (`@fontsource-variable/*`). The CSP is `default-src 'self'`, so `vite.config.ts` sets `assetsInlineLimit: 0` (inlined `data:` fonts are blocked); keep it

### State Management
- Server state: TanStack Query. Query keys MUST include the user id (`['conversations', uid, ...]`) and the cache is cleared on sign-out, so one account's data can never render for another on the same tab. API calls go through `useApiClient()` + `unwrap()` (typed from the spec, token + refresh handled), never raw `fetch`. Client state: React state/context. Auth state: `useAuth()`
- Page tests that render the signed-in app need `/conversations` answered: use `signedIn()` (empty list) or a stateful fake like `components/sidebar/sidebar.test.tsx`

### Routing
- React Router 8, declarative (SPA) mode. Public routes live under `PublicOnly`, everything else under `RequireAuth` (redirects to `/login`, then back)

### Build-time config
- `VITE_REGISTRATION_MODE=open` hides the invite field (the API does not expose its mode). Default `invite`

### Lockfile gotcha
- Regenerate lockfiles with `make npm-lock` after changing JS dependencies (uses the container's npm). A lock written by a different npm can omit optional platform packages and `npm ci` then fails in Docker/CI

---

## Running Locally

```bash
cd frontend
npm ci
npm run gen:api      # regenerate src/api/schema.ts from docs/02_outputs/04_api_spec.yaml
npm run dev          # http://localhost:5173, proxies /api to http://localhost:8000
```

## Running Tests

```bash
cd frontend && npm test                 # vitest + Testing Library
npm run lint && npm run typecheck       # eslint, prettier --check, tsc
make up && make verify                  # real-browser e2e (tests/e2e) + DOM checks against the stack (http://localhost:8080)
```

---

## Rules for AI Tools

- **Never write API client code manually** — regenerate from the OpenAPI spec
- After any change to a page or component, run `make verify`
- Read `memory/auth.md` before touching auth-related components
- All new pages need a route defined and an e2e test in `tests/e2e/`
- Do not import from `backend/` — all data flows through the API layer
- Read `docs/02_outputs/07_design_system.md` before modifying any styles, themes, or design tokens. Do not introduce colors or fonts not defined there.
