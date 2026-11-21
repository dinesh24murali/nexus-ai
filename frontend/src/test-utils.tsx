import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

export type Call = { path: string; method: string; body: unknown; headers: Headers }
export type Handler = (call: Call) => Response | Promise<Response>

/** Render the whole app at `path`, with `fetch` replaced by `handler` and every call recorded. */
export function renderApp(path: string, handler: Handler) {
  const calls: Call[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const call: Call = {
      path: input.toString(),
      method: init.method ?? 'GET',
      body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
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
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider manager={manager}>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
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
