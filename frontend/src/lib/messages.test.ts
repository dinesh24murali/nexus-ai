import { describe, expect, it } from 'vitest'
import { ApiProblem } from './auth'
import { describeProblem, passwordStrength } from './messages'

const problem = (code: string, extra: Partial<ConstructorParameters<typeof ApiProblem>> = []) =>
  new ApiProblem(400, code, 'server text', ...(extra as []))

describe('describeProblem', () => {
  it.each([
    ['INVALID_CREDENTIALS', 'Email or password is incorrect.'],
    ['INVITE_INVALID', 'This invite code is invalid, used or expired.'],
    ['EMAIL_TAKEN', 'An account with this email already exists.'],
  ])('%s has the spec wording', (code, text) => {
    expect(describeProblem(problem(code)).message).toBe(text)
  })

  it('tells the user how long to wait when rate limited', () => {
    const e = new ApiProblem(429, 'RATE_LIMITED', 'x', undefined, undefined, 540)
    expect(describeProblem(e).message).toBe('Too many attempts. Try again in 9 minutes.')
    const e2 = new ApiProblem(429, 'RATE_LIMITED', 'x', undefined, undefined, 30)
    expect(describeProblem(e2).message).toContain('30 seconds')
    expect(describeProblem(new ApiProblem(429, 'RATE_LIMITED', 'x')).message).toContain(
      'a few minutes',
    )
  })

  it('maps validation failures onto fields', () => {
    const e = new ApiProblem(422, 'VALIDATION_FAILED', 'x', {
      fields: [
        { loc: ['body', 'password'], msg: 'String should have at least 10 characters' },
        { loc: ['body', 'email'], msg: 'Value error, value is not a valid email address' },
      ],
    })
    const out = describeProblem(e)
    expect(out.fields.password).toBe('String should have at least 10 characters')
    expect(out.fields.email).toBe('value is not a valid email address')
  })

  it('never shows raw server text for unknown errors', () => {
    expect(describeProblem(problem('SOMETHING_NEW')).message).not.toContain('server text')
  })

  it('treats a network failure as a connectivity problem', () => {
    expect(describeProblem(new TypeError('Failed to fetch')).message).toContain('Could not reach')
  })
})

describe('passwordStrength', () => {
  it.each([
    ['', 0, ''],
    ['short', 0, 'Too short'],
    ['alllowercase', 1, 'Fair'],
    ['Mixed1234', 0, 'Too short'],
    ['Passw0rdxx', 2, 'Good'], // 10 chars, 3 classes: acceptable, not yet strong
    ['lower-and-UPPER', 3, 'Strong'], // 15 chars, 3 classes
    ['correct-horse-battery', 3, 'Strong'],
  ])('%j -> score %i (%s)', (pw, score, label) => {
    expect(passwordStrength(pw)).toEqual({ score, label })
  })
})
