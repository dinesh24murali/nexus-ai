import { ApiProblem } from './auth'

export type FormError = {
  /** Banner text for the whole form. */
  message: string
  /** Per-field messages, keyed by request field name (email, password, display_name, invite_code). */
  fields: Record<string, string>
}

function waitText(seconds?: number): string {
  if (!seconds) return 'a few minutes'
  if (seconds < 60) return `${seconds} second${seconds === 1 ? '' : 's'}`
  const minutes = Math.ceil(seconds / 60)
  return `${minutes} minute${minutes === 1 ? '' : 's'}`
}

/** Friendly, non-leaky text for an API failure (messages mirror functional spec FS-001). */
export function describeProblem(error: unknown): FormError {
  if (!(error instanceof ApiProblem)) {
    return {
      message: 'Could not reach the server. Check your connection and try again.',
      fields: {},
    }
  }
  switch (error.code) {
    case 'INVALID_CREDENTIALS':
      return { message: 'Email or password is incorrect.', fields: {} }
    case 'INVITE_INVALID':
      return { message: 'This invite code is invalid, used or expired.', fields: {} }
    case 'EMAIL_TAKEN':
      return { message: 'An account with this email already exists.', fields: {} }
    case 'ACCOUNT_DISABLED':
      return { message: 'This account has been disabled. Contact the administrator.', fields: {} }
    case 'RATE_LIMITED':
      return {
        message: `Too many attempts. Try again in ${waitText(error.retryAfterS)}.`,
        fields: {},
      }
    case 'VALIDATION_FAILED': {
      const raw = (
        error.details as { fields?: { loc: (string | number)[]; msg: string }[] } | undefined
      )?.fields
      const fields: Record<string, string> = {}
      for (const f of raw ?? []) {
        const name = String(f.loc[f.loc.length - 1])
        fields[name] ??= f.msg.replace(/^Value error, /, '')
      }
      return { message: 'Please check the highlighted fields.', fields }
    }
    default:
      return { message: 'Something went wrong. Please try again.', fields: {} }
  }
}

export type Strength = { score: 0 | 1 | 2 | 3; label: string }

/** A hint, not a policy: the server enforces length (10-128). */
export function passwordStrength(password: string): Strength {
  if (password.length === 0) return { score: 0, label: '' }
  if (password.length < 10) return { score: 0, label: 'Too short' }
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length
  if (password.length >= 16 || (password.length >= 12 && classes >= 3)) {
    return { score: 3, label: 'Strong' }
  }
  return classes >= 2 ? { score: 2, label: 'Good' } : { score: 1, label: 'Fair' }
}
