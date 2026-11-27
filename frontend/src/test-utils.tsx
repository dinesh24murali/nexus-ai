import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode, type ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { vi } from 'vitest'
import App from './App'
import { AuthManager } from './lib/auth'
import { AuthProvider } from './lib/AuthProvider'

export const USER = {
  id: '7c1e2f0a-3b4d-4e5f-8a9b-0c1d2e3f4a5b',
  email: 'marco@example.com',
  display_name: 'Marco',
  memory_enabled: true,
  created_at: '2026-11-19T10:00:00Z',
}

export const authBody = (token = 'tok-1') => ({
  access_token: token,
  token_type: 'bearer',
  expires_in: 900,
  user: USER,
})

export const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })

export const problem = (
  status: number,
  code: string,
  message = 'server message',
  extra: Record<string, unknown> = {},
  headers: Record<string, string> = {},
) => json(status, { error: { code, message, request_id: 'req_t', ...extra } }, headers)

export type Call = {
  path: string
  method: string
  /** The JSON body, parsed (undefined for anything that is not a string, e.g. an upload). */
  body: unknown
  /** The body exactly as handed to `fetch` (an ArrayBuffer for a multipart upload). */
  rawBody: unknown
  headers: Headers
}
export type Handler = (call: Call) => Response | Promise<Response>

/** Render the whole app at `path`, with `fetch` replaced by `handler` and every call recorded. */
export function renderApp(path: string, handler: Handler, options: { strict?: boolean } = {}) {
  const calls: Call[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const call: Call = {
      path: input.toString(),
      method: init.method ?? 'GET',
      body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
      rawBody: init.body,
      headers: new Headers(init.headers),
    }
    calls.push(call)
    return handler(call)
  })
  const manager = new AuthManager({ fetch: fetchMock as unknown as typeof fetch, locks: null })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  })
  const user = userEvent.setup()
  const wrap = (tree: ReactNode) => (options.strict ? <StrictMode>{tree}</StrictMode> : tree)
  const utils = render(
    wrap(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          <AuthProvider manager={manager}>
            <App />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  )
  return { ...utils, user, calls, manager }
}

/** Handler for a visitor with no session: refresh is rejected; extra routes can be added. */
export const anonymous =
  (extra: Handler = () => problem(500, 'INTERNAL_ERROR')): Handler =>
  (call) =>
    call.path.endsWith('/auth/refresh') ? problem(401, 'UNAUTHORIZED') : extra(call)

export const emptyPage = { items: [], next_cursor: null }

/** Handler for a signed-in user (silent refresh succeeds); the sidebar sees no conversations. */
export const signedIn =
  (extra: Handler = () => problem(500, 'INTERNAL_ERROR')): Handler =>
  (call) => {
    if (call.path.endsWith('/auth/refresh')) return json(200, authBody())
    if (call.method === 'GET' && /\/conversations(\?|$)/.test(call.path)) {
      return json(200, emptyPage)
    }
    return extra(call)
  }

// ---------------------------------------------------------------- streaming (SSE) helpers

type StreamEventLike = { type: string } & Record<string, unknown>

const frame = (event: StreamEventLike, id: number) =>
  `event: ${event.type}\nid: ${id}\ndata: ${JSON.stringify(event)}\n\n`

const SSE_HEADERS = { 'content-type': 'text/event-stream' }

/** A whole stream, delivered at once. */
export const sseResponse = (events: StreamEventLike[]) =>
  new Response(events.map((e, i) => frame(e, i + 1)).join(''), {
    status: 200,
    headers: SSE_HEADERS,
  })

/** A stream the test feeds event by event (so it can look at the screen in between). */
export function liveStream() {
  const encoder = new TextEncoder()
  let controller!: ReadableStreamDefaultController<Uint8Array>
  let id = 0
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  return {
    response: new Response(body, { status: 200, headers: SSE_HEADERS }),
    push: (event: StreamEventLike) => controller.enqueue(encoder.encode(frame(event, ++id))),
    ping: () => controller.enqueue(encoder.encode(': ping\n\n')),
    end: () => controller.close(),
    fail: (error: unknown = new TypeError('network error')) => controller.error(error),
  }
}

export const IDS = {
  run: '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d',
  user: '6c7d8e9f-0a1b-4c2d-8e3f-4a5b6c7d8e9f',
  assistant: '5b6c7d8e-1a2b-4c3d-9e8f-0a1b2c3d4e5f',
}

export const runStarted = (): StreamEventLike => ({
  type: 'run_started',
  run_id: IDS.run,
  user_message_id: IDS.user,
  assistant_message_id: IDS.assistant,
})
export const token = (delta: string): StreamEventLike => ({ type: 'token', delta })
export const usageEvent = (): StreamEventLike => ({
  type: 'usage',
  usage: {
    model: 'fake',
    prompt_tokens: 10,
    cached_tokens: 0,
    completion_tokens: 5,
    cost_usd: 0.001,
  },
})
export const done = (finish_reason = 'stop'): StreamEventLike => ({
  type: 'done',
  message_id: IDS.assistant,
  finish_reason,
})

/** A complete, successful stream whose text is `text`. */
export const answerStream = (text: string) =>
  sseResponse([runStarted(), ...text.split(/(?<= )/).map(token), usageEvent(), done()])

/**
 * Wait until `text` is on screen. Unlike `expect(await findByText(..)).toBeInTheDocument()` this
 * looks the element up again at the end: while an answer streams its node is replaced by the
 * stored message's, so an element found a moment ago may already be gone.
 */
export const seen = (text: string | RegExp) =>
  waitFor(() => expect(screen.getByText(text)).toBeInTheDocument())
