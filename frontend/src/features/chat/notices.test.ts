import { describe, expect, it } from 'vitest'
import { ApiProblem } from '../../lib/auth'
import { StreamInterrupted } from '../../api/stream'
import { describeNotice, formatResetTime, noticeFromError } from './notices'

const problem = (
  status: number,
  code: string,
  details?: Record<string, unknown>,
  retryAfterS?: number,
) => new ApiProblem(status, code, 'server text', details, 'req_1', retryAfterS)

describe('noticeFromError', () => {
  it('maps the refusals the API sends before a stream opens', () => {
    expect(noticeFromError(problem(400, 'MODERATION_FLAGGED'))).toEqual({ kind: 'moderation' })
    expect(
      noticeFromError(
        problem(429, 'BUDGET_EXCEEDED', { scope: 'global', resets_at: '2026-11-22T00:00:00Z' }),
      ),
    ).toEqual({ kind: 'budget', scope: 'global', resetsAt: '2026-11-22T00:00:00Z' })
    expect(noticeFromError(problem(429, 'RATE_LIMITED', undefined, 17))).toEqual({
      kind: 'rate_limit',
      retryAfterS: 17,
    })
    expect(noticeFromError(problem(503, 'SERVICE_RESTARTING'))).toEqual({ kind: 'restarting' })
    expect(noticeFromError(problem(409, 'RUN_IN_PROGRESS'))).toEqual({ kind: 'busy' })
  })

  it('maps an approval that is no longer waiting to its own notice', () => {
    expect(noticeFromError(problem(409, 'NO_PENDING_INTERRUPT'))).toEqual({ kind: 'no_approval' })
    expect(describeNotice({ kind: 'no_approval' })).toEqual({
      message: 'This approval is no longer waiting for an answer. Send a message to continue.',
      action: null,
    })
  })

  it('treats a budget with an unknown scope as the user budget', () => {
    expect(noticeFromError(problem(429, 'BUDGET_EXCEEDED', {}))).toMatchObject({
      kind: 'budget',
      scope: 'user',
      resetsAt: null,
    })
  })

  it('reports a broken stream as a lost connection', () => {
    expect(noticeFromError(new StreamInterrupted())).toEqual({ kind: 'connection_lost' })
  })

  it('falls back to a generic failure for anything else, never echoing server text', () => {
    const notice = noticeFromError(problem(500, 'INTERNAL_ERROR'))
    expect(notice).toEqual({ kind: 'failed' })
    expect(noticeFromError(new TypeError('Failed to fetch'))).toEqual({ kind: 'connection_lost' })
  })
})

describe('formatResetTime', () => {
  it('shows the reset moment as UTC clock time', () => {
    expect(formatResetTime('2026-11-22T00:00:00Z')).toBe('00:00 UTC')
    expect(formatResetTime('2026-11-22T09:05:00+00:00')).toBe('09:05 UTC')
  })

  it('has a sensible default when the time is missing or unreadable', () => {
    expect(formatResetTime(null)).toBe('00:00 UTC')
    expect(formatResetTime('not a date')).toBe('00:00 UTC')
  })
})

describe('describeNotice', () => {
  it('words the user budget and the global cap differently, both with the reset time', () => {
    const user = describeNotice({ kind: 'budget', scope: 'user', resetsAt: '2026-11-22T00:00:00Z' })
    const all = describeNotice({
      kind: 'budget',
      scope: 'global',
      resetsAt: '2026-11-22T00:00:00Z',
    })

    expect(user.message).toMatch(/your daily/i)
    expect(all.message).toMatch(/everyone/i)
    expect(user.message).toContain('00:00 UTC')
    expect(all.message).toContain('00:00 UTC')
  })

  it('tells the user how long to wait after a rate limit', () => {
    expect(describeNotice({ kind: 'rate_limit', retryAfterS: 17 }).message).toContain('17 seconds')
    expect(describeNotice({ kind: 'rate_limit', retryAfterS: 90 }).message).toContain('2 minutes')
    expect(describeNotice({ kind: 'rate_limit', retryAfterS: undefined }).message).toMatch(/moment/)
  })

  it('offers Reload only for a lost connection', () => {
    expect(describeNotice({ kind: 'connection_lost' }).action).toBe('reload')
    expect(describeNotice({ kind: 'run_error', message: 'x', retryable: true }).action).toBeNull()
    expect(describeNotice({ kind: 'moderation' }).action).toBeNull()
  })

  it('uses the server-provided, user-safe text of a run error', () => {
    const text = 'The model is unavailable right now. Try again.'
    expect(describeNotice({ kind: 'run_error', message: text, retryable: true }).message).toBe(text)
  })
})
