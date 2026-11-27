import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiProblem, AuthManager } from './auth'

const USER = {
  id: '7c1e2f0a-3b4d-4e5f-8a9b-0c1d2e3f4a5b',
  email: 'marco@example.com',
  display_name: 'Marco',
  memory_enabled: true,
  created_at: '2026-11-19T10:00:00Z',
}

function authBody(token: string) {
  return { access_token: token, token_type: 'bearer', expires_in: 900, user: USER }
}

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

function problem(status: number, code: string, message = 'msg', headers = {}) {
  return json(status, { error: { code, message, request_id: 'req_x' } }, headers)
}

/** A fetch whose responses are decided by a function of (path, init); records every call. */
function scriptedFetch(handler: (path: string, init: RequestInit) => Response | Promise<Response>) {
  const calls: { path: string; init: RequestInit }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = typeof input === 'string' ? input : input.toString()
    calls.push({ path, init })
    return handler(path, init)
  })
  return { fn: fn as unknown as typeof fetch, calls }
}

const bearerOf = (init: RequestInit) => new Headers(init.headers).get('authorization')

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => vi.restoreAllMocks())

describe('login', () => {
  it('stores the access token in memory only and exposes the user', async () => {
    const { fn } = scriptedFetch(() => json(200, authBody('tok-1')))
    const auth = new AuthManager({ fetch: fn, locks: null })

    await auth.login('marco@example.com', 'correct-horse-battery')

    expect(auth.getState()).toMatchObject({ status: 'authenticated', user: USER })
    expect(auth.getAccessToken()).toBe('tok-1')
    // Never in any browser storage or readable cookie.
    expect(JSON.stringify({ ...localStorage })).not.toContain('tok-1')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('tok-1')
    expect(document.cookie).not.toContain('tok-1')
  })

  it('throws a typed problem and stays anonymous on failure', async () => {
    const { fn } = scriptedFetch(() =>
      problem(401, 'INVALID_CREDENTIALS', 'Email or password is incorrect.'),
    )
    const auth = new AuthManager({ fetch: fn, locks: null })

    const err = await auth.login('a@b.co', 'wrong-password-1').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiProblem)
    expect(err).toMatchObject({ status: 401, code: 'INVALID_CREDENTIALS' })
    expect(auth.getState().status).not.toBe('authenticated')
    expect(auth.getAccessToken()).toBeNull()
  })

  it('exposes Retry-After on rate limiting', async () => {
    const { fn } = scriptedFetch(() =>
      problem(429, 'RATE_LIMITED', 'slow down', { 'retry-after': '540' }),
    )
    const auth = new AuthManager({ fetch: fn, locks: null })
    const err = (await auth.login('a@b.co', 'x'.repeat(12)).catch((e: unknown) => e)) as ApiProblem
    expect(err.code).toBe('RATE_LIMITED')
    expect(err.retryAfterS).toBe(540)
  })

  it('survives a non-JSON error body (e.g. a proxy 502)', async () => {
    const { fn } = scriptedFetch(() => new Response('<html>Bad gateway</html>', { status: 502 }))
    const auth = new AuthManager({ fetch: fn, locks: null })
    const err = (await auth.login('a@b.co', 'x'.repeat(12)).catch((e: unknown) => e)) as ApiProblem
    expect(err).toBeInstanceOf(ApiProblem)
    expect(err.status).toBe(502)
    expect(err.code).toBe('HTTP_ERROR')
  })
})

describe('updateUser', () => {
  it('replaces the signed-in profile and tells subscribers, keeping the session', async () => {
    const { fn } = scriptedFetch(() => json(200, authBody('tok-1')))
    const auth = new AuthManager({ fetch: fn, locks: null })
    await auth.login('marco@example.com', 'correct-horse-battery')
    const heard = vi.fn()
    auth.subscribe(heard)

    auth.updateUser({ ...USER, memory_enabled: false })

    expect(auth.getState().user?.memory_enabled).toBe(false)
    expect(auth.getState().status).toBe('authenticated')
    expect(auth.getAccessToken()).toBe('tok-1')
    expect(heard).toHaveBeenCalledTimes(1)
  })

  it('does nothing when nobody is signed in (a late response after sign-out)', () => {
    const { fn } = scriptedFetch(() => json(500, {}))
    const auth = new AuthManager({ fetch: fn, locks: null })

    auth.updateUser(USER)

    expect(auth.getState().user).toBeNull()
  })
})

describe('bootstrap (silent sign-in on page load)', () => {
  it('signs in from the refresh cookie when it is valid', async () => {
    const { fn, calls } = scriptedFetch(() => json(200, authBody('tok-boot')))
    const auth = new AuthManager({ fetch: fn, locks: null })
    await auth.bootstrap()
    expect(auth.getState().status).toBe('authenticated')
    expect(calls[0].path).toBe('/api/v1/auth/refresh')
    expect(calls[0].init.method).toBe('POST')
    expect(calls[0].init.credentials).toBe('include')
  })

  it('becomes anonymous, without a "session expired" notice, when there is no session', async () => {
    const { fn } = scriptedFetch(() => problem(401, 'UNAUTHORIZED'))
    const auth = new AuthManager({ fetch: fn, locks: null })
    await auth.bootstrap()
    expect(auth.getState()).toMatchObject({ status: 'anonymous', sessionExpired: false })
  })

  it('shares ONE request between concurrent calls (React StrictMode mounts twice)', async () => {
    // A second concurrent refresh with the same cookie would look like token reuse to the server
    // and revoke the whole session family.
    const { fn, calls } = scriptedFetch(() => json(200, authBody('tok-once')))
    const auth = new AuthManager({ fetch: fn, locks: null })
    await Promise.all([auth.bootstrap(), auth.bootstrap()])
    expect(calls.filter((c) => c.path.endsWith('/auth/refresh'))).toHaveLength(1)
  })
})

describe('authenticated fetch', () => {
  async function signedIn(
    handler: (path: string, init: RequestInit) => Response | Promise<Response>,
  ) {
    const sf = scriptedFetch((path, init) =>
      path.endsWith('/auth/login') ? json(200, authBody('tok-1')) : handler(path, init),
    )
    const auth = new AuthManager({ fetch: sf.fn, locks: null })
    await auth.login('marco@example.com', 'correct-horse-battery')
    return { auth, calls: sf.calls }
  }

  it('sends the bearer token', async () => {
    const { auth, calls } = await signedIn(() => json(200, USER))
    await auth.fetch('/api/v1/me')
    expect(bearerOf(calls.at(-1)!.init)).toBe('Bearer tok-1')
  })

  it('refreshes once and retries when the access token expired', async () => {
    let meCalls = 0
    const { auth, calls } = await signedIn((path) => {
      if (path.endsWith('/auth/refresh')) return json(200, authBody('tok-2'))
      meCalls += 1
      return meCalls === 1 ? problem(401, 'TOKEN_EXPIRED') : json(200, USER)
    })
    const res = await auth.fetch('/api/v1/me')
    expect(res.status).toBe(200)
    const me = calls.filter((c) => c.path === '/api/v1/me')
    expect(me).toHaveLength(2)
    expect(bearerOf(me[1].init)).toBe('Bearer tok-2')
    expect(auth.getAccessToken()).toBe('tok-2')
  })

  it('many parallel expired requests trigger exactly ONE refresh', async () => {
    let refreshed = false
    const { auth, calls } = await signedIn(async (path) => {
      if (path.endsWith('/auth/refresh')) {
        await new Promise((r) => setTimeout(r, 10))
        refreshed = true
        return json(200, authBody('tok-2'))
      }
      return refreshed ? json(200, USER) : problem(401, 'TOKEN_EXPIRED')
    })
    const results = await Promise.all(Array.from({ length: 6 }, () => auth.fetch('/api/v1/me')))
    expect(results.every((r) => r.status === 200)).toBe(true)
    expect(calls.filter((c) => c.path.endsWith('/auth/refresh'))).toHaveLength(1)
  })

  it('gives up after one retry: a second 401 ends the session, no loop', async () => {
    const { auth, calls } = await signedIn((path) =>
      path.endsWith('/auth/refresh') ? json(200, authBody('tok-2')) : problem(401, 'UNAUTHORIZED'),
    )
    const res = await auth.fetch('/api/v1/me')
    expect(res.status).toBe(401)
    expect(calls.filter((c) => c.path === '/api/v1/me')).toHaveLength(2)
    expect(auth.getState()).toMatchObject({ status: 'anonymous', sessionExpired: true })
  })

  it('ends the session with a notice when the refresh token is rejected', async () => {
    const { auth } = await signedIn((path) =>
      path.endsWith('/auth/refresh') ? problem(401, 'UNAUTHORIZED') : problem(401, 'TOKEN_EXPIRED'),
    )
    await auth.fetch('/api/v1/me')
    expect(auth.getState()).toMatchObject({ status: 'anonymous', sessionExpired: true, user: null })
    expect(auth.getAccessToken()).toBeNull()
  })

  it('does not refresh for other errors', async () => {
    const { auth, calls } = await signedIn(() => problem(500, 'INTERNAL_ERROR'))
    const res = await auth.fetch('/api/v1/me')
    expect(res.status).toBe(500)
    expect(calls.filter((c) => c.path.endsWith('/auth/refresh'))).toHaveLength(0)
    expect(auth.getState().status).toBe('authenticated')
  })

  it('never refreshes because an /auth/* call itself returned 401', async () => {
    const { fn, calls } = scriptedFetch(() => problem(401, 'INVALID_CREDENTIALS'))
    const auth = new AuthManager({ fetch: fn, locks: null })
    await auth.login('a@b.co', 'x'.repeat(12)).catch(() => undefined)
    expect(calls).toHaveLength(1)
  })

  it('does not hand the bearer token to other origins', async () => {
    const { auth, calls } = await signedIn(() => json(200, {}))
    await auth.fetch('https://evil.example.com/steal')
    expect(bearerOf(calls.at(-1)!.init)).toBeNull()
  })
})

describe('cross-tab safety', () => {
  it('runs the refresh inside a Web Lock so two tabs never rotate at once', async () => {
    const order: string[] = []
    const locks = {
      request: vi.fn(async (_name: string, cb: () => Promise<unknown>) => {
        order.push('lock-acquired')
        const r = await cb()
        order.push('lock-released')
        return r
      }),
    }
    const { fn } = scriptedFetch(() => {
      order.push('network')
      return json(200, authBody('tok-lock'))
    })
    const auth = new AuthManager({ fetch: fn, locks: locks as never })
    await auth.bootstrap()
    expect(locks.request).toHaveBeenCalledWith('nexus-refresh', expect.any(Function))
    expect(order).toEqual(['lock-acquired', 'network', 'lock-released'])
  })
})

describe('logout', () => {
  it('clears local state even if the server call fails', async () => {
    const sf = scriptedFetch((path) =>
      path.endsWith('/auth/login') ? json(200, authBody('tok-1')) : problem(500, 'INTERNAL_ERROR'),
    )
    const auth = new AuthManager({ fetch: sf.fn, locks: null })
    await auth.login('marco@example.com', 'correct-horse-battery')
    await auth.logout()
    expect(auth.getState()).toMatchObject({
      status: 'anonymous',
      user: null,
      sessionExpired: false,
    })
    expect(auth.getAccessToken()).toBeNull()
    expect(sf.calls.some((c) => c.path.endsWith('/auth/logout'))).toBe(true)
  })
})

describe('subscribe', () => {
  it('notifies listeners on every state change and supports unsubscribe', async () => {
    const { fn } = scriptedFetch(() => json(200, authBody('tok-1')))
    const auth = new AuthManager({ fetch: fn, locks: null })
    const seen: string[] = []
    const off = auth.subscribe(() => seen.push(auth.getState().status))
    await auth.login('marco@example.com', 'correct-horse-battery')
    off()
    await auth.logout()
    expect(seen).toEqual(['authenticated'])
  })
})
