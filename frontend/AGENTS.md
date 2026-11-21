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
│   ├── components/               ← app components (ThemeToggle, sidebar/: ConversationNav, ConversationList, ConversationItem)
│   ├── features/                 ← feature folders (conversations/ = TanStack Query hooks; chat/ = ChatView, useChatRun,
│   │                               MessageList, Composer, Feedback (thumbs), Markdown + CodeBlock + highlight (Shiki), notices, UsageMeter;
│   │                               files/, settings/ to come)
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
