/**
 * Session management for the SPA.
 *
 * - The access token lives in memory only (never localStorage / sessionStorage / readable cookies).
 *   The refresh token is an HttpOnly cookie the browser manages; JS never sees it.
 * - Refreshing ROTATES the refresh token, and the server treats reuse of an old one as theft and
 *   revokes the whole session. So refreshes must never overlap: one in flight per tab (shared
 *   promise) and one at a time across tabs (Web Locks). See memory/auth.md.
 */
import type { components } from '../api/schema'

export type User = components['schemas']['User']
type AuthResponse = components['schemas']['AuthResponse']
type RegisterRequest = components['schemas']['RegisterRequest']

export type AuthState = {
  status: 'loading' | 'authenticated' | 'anonymous'
  user: User | null
  /** True when an established session died (refresh rejected): show "Your session expired". */
  sessionExpired: boolean
}

export class ApiProblem extends Error {
  readonly status: number
  readonly code: string
  readonly details?: Record<string, unknown>
  readonly requestId?: string
  readonly retryAfterS?: number

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, unknown>,
    requestId?: string,
    retryAfterS?: number,
  ) {
    super(message)
    this.name = 'ApiProblem'
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
    this.retryAfterS = retryAfterS
  }
}

/** Turn any failed response into an ApiProblem, tolerating non-JSON bodies (proxy errors). */
export async function parseProblem(res: Response): Promise<ApiProblem> {
  const retryAfter = Number(res.headers.get('retry-after'))
  const retryAfterS = Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined
  try {
    const body = (await res.json()) as {
      error?: {
        code?: string
        message?: string
        details?: Record<string, unknown>
        request_id?: string
      }
    }
    if (body.error?.code) {
      const { code, message, details, request_id } = body.error
      return new ApiProblem(res.status, code, message ?? code, details, request_id, retryAfterS)
    }
  } catch {
    // fall through: not our error envelope
  }
  return new ApiProblem(
    res.status,
    'HTTP_ERROR',
    `Request failed (${res.status}).`,
    undefined,
    undefined,
    retryAfterS,
  )
}

type Locks = Pick<LockManager, 'request'> | null

export type AuthDeps = {
  fetch: typeof fetch
  /** Web Locks implementation; `null` disables cross-tab locking (tests, old browsers). */
  locks?: Locks
  baseUrl?: string
}

const REFRESH_LOCK = 'nexus-refresh'

export class AuthManager {
  private state: AuthState = { status: 'loading', user: null, sessionExpired: false }
  private token: string | null = null
  private listeners = new Set<() => void>()
  private refreshing: Promise<boolean> | null = null
  private readonly base: string
  private readonly locks: Locks
  private readonly rawFetch: typeof fetch

  constructor(deps: AuthDeps) {
    this.rawFetch = deps.fetch
    this.base = deps.baseUrl ?? '/api/v1'
    this.locks = deps.locks === undefined ? (globalThis.navigator?.locks ?? null) : deps.locks
  }

  // ── state ────────────────────────────────────────────────────────────────
  getState = (): AuthState => this.state
  getAccessToken = (): string | null => this.token

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private set(state: AuthState) {
    this.state = state
    this.listeners.forEach((l) => l())
  }

  private setSession(body: AuthResponse) {
    this.token = body.access_token
    this.set({ status: 'authenticated', user: body.user, sessionExpired: false })
  }

  private endSession(expired: boolean) {
    this.token = null
    this.set({ status: 'anonymous', user: null, sessionExpired: expired })
  }

  // ── use cases ────────────────────────────────────────────────────────────
  /** Silent sign-in on page load, from the refresh cookie. Never shows "session expired". */
  async bootstrap(): Promise<void> {
    try {
      if (!(await this.refresh())) this.endSession(false)
    } catch {
      this.endSession(false) // offline / server down: let the user reach the login screen
    }
  }

  async login(email: string, password: string): Promise<void> {
    await this.authenticate('/auth/login', { email, password })
  }

  async register(body: RegisterRequest): Promise<void> {
    await this.authenticate('/auth/register', body)
  }

  private async authenticate(path: string, body: unknown): Promise<void> {
    const res = await this.rawFetch(`${this.base}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw await parseProblem(res)
    this.setSession((await res.json()) as AuthResponse)
  }

  async logout(): Promise<void> {
    const call = () => this.send(`${this.base}/auth/logout`, { method: 'POST' })
    try {
      let res = await call()
      if (res.status === 401 && (await this.refresh())) res = await call() // expired token: get a fresh one so the server revokes the session
      void res
    } catch {
      // Best effort: the user asked to sign out, so local state is cleared regardless.
    }
    this.endSession(false)
  }

  // ── refresh (single-flight, cross-tab safe) ──────────────────────────────
  /** True if a new access token was obtained; false if the refresh token was rejected. */
  refresh(): Promise<boolean> {
    if (!this.refreshing) {
      const run = () => this.doRefresh()
      const guarded = this.locks ? this.locks.request(REFRESH_LOCK, run) : run()
      this.refreshing = Promise.resolve(guarded).finally(() => {
        this.refreshing = null
      })
    }
    return this.refreshing
  }

  private async doRefresh(): Promise<boolean> {
    const res = await this.rawFetch(`${this.base}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    })
    if (res.ok) {
      this.setSession((await res.json()) as AuthResponse)
      return true
    }
    if (res.status === 401 || res.status === 403) return false
    throw await parseProblem(res) // 5xx etc.: the session may still be fine, don't end it
  }

  // ── authenticated fetch ──────────────────────────────────────────────────
  private isApiUrl(url: string): boolean {
    if (url.startsWith('//')) return false
    if (url.startsWith('/')) return true
    try {
      return new URL(url).origin === globalThis.location?.origin
    } catch {
      return false
    }
  }

  private send(url: string, init: RequestInit): Promise<Response> {
    const headers = new Headers(init.headers)
    if (this.token && this.isApiUrl(url)) headers.set('authorization', `Bearer ${this.token}`)
    return this.rawFetch(url, {
      ...init,
      headers,
      ...(this.isApiUrl(url) ? { credentials: 'include' as const } : {}),
    })
  }

  /**
   * fetch() with the bearer token. A 401 on a normal API call triggers ONE refresh and ONE retry;
   * if that fails too the session is over. Tokens are only ever sent to our own origin.
   */
  async fetch(input: string | URL, init: RequestInit = {}): Promise<Response> {
    const url = input.toString()
    const used = this.token
    const res = await this.send(url, init)
    if (res.status !== 401 || !this.isApiUrl(url) || url.includes('/auth/')) return res

    // Someone else (another request) may already have refreshed while this one was in flight.
    const alreadyRefreshed = this.token !== null && this.token !== used
    if (!alreadyRefreshed && !(await this.refresh())) {
      this.endSession(true)
      return res
    }
    const retry = await this.send(url, init)
    if (retry.status === 401) this.endSession(true)
    return retry
  }
}
