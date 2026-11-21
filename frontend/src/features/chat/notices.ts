import { StreamInterrupted } from '../../api/stream'
import { ApiProblem } from '../../lib/auth'

/** Everything that can stop or spoil a send, as the UI shows it. */
export type Notice =
  | { kind: 'moderation' }
  | { kind: 'budget'; scope: 'user' | 'global'; resetsAt: string | null }
  | { kind: 'rate_limit'; retryAfterS: number | undefined }
  | { kind: 'restarting' }
  | { kind: 'busy' }
  | { kind: 'no_approval' }
  | { kind: 'run_error'; message: string; retryable: boolean }
  | { kind: 'connection_lost' }
  | { kind: 'failed' }

export function noticeFromError(error: unknown): Notice {
  if (error instanceof StreamInterrupted) return { kind: 'connection_lost' }
  if (error instanceof ApiProblem) {
    switch (error.code) {
      case 'MODERATION_FLAGGED':
        return { kind: 'moderation' }
      case 'BUDGET_EXCEEDED': {
        const details = error.details as { scope?: string; resets_at?: string } | undefined
        return {
          kind: 'budget',
          scope: details?.scope === 'global' ? 'global' : 'user',
          resetsAt: details?.resets_at ?? null,
        }
      }
      case 'RATE_LIMITED':
        return { kind: 'rate_limit', retryAfterS: error.retryAfterS }
      case 'SERVICE_RESTARTING':
        return { kind: 'restarting' }
      case 'RUN_IN_PROGRESS':
        return { kind: 'busy' }
      case 'NO_PENDING_INTERRUPT':
        return { kind: 'no_approval' }
      default:
        return { kind: 'failed' }
    }
  }
  // fetch() itself failing (offline, DNS, proxy down) surfaces as a TypeError.
  if (error instanceof TypeError) return { kind: 'connection_lost' }
  return { kind: 'failed' }
}

/** "00:00 UTC": budgets reset at the UTC midnight, wherever the user is. */
export function formatResetTime(iso: string | null): string {
  const date = iso ? new Date(iso) : null
  if (!date || Number.isNaN(date.getTime())) return '00:00 UTC'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`
}

function waitText(seconds: number | undefined): string {
  if (!seconds) return 'a moment'
  if (seconds < 60) return `${seconds} second${seconds === 1 ? '' : 's'}`
  const minutes = Math.ceil(seconds / 60)
  return `${minutes} minute${minutes === 1 ? '' : 's'}`
}

export type NoticeView = { message: string; action: 'reload' | null }

export function describeNotice(notice: Notice): NoticeView {
  switch (notice.kind) {
    case 'moderation':
      return {
        message:
          'This message was not sent because it may violate the content policy. Please rephrase it and try again.',
        action: null,
      }
    case 'budget': {
      const reset = formatResetTime(notice.resetsAt)
      return {
        message:
          notice.scope === 'global'
            ? `Nexus has reached its daily limit for everyone. It resets at ${reset}.`
            : `You've reached your daily usage limit. It resets at ${reset}.`,
        action: null,
      }
    }
    case 'rate_limit':
      return {
        message: `You're sending messages too quickly. Try again in ${waitText(notice.retryAfterS)}.`,
        action: null,
      }
    case 'restarting':
      return { message: 'Nexus is restarting, try again in a moment.', action: null }
    case 'busy':
      return {
        message: 'This conversation is already answering. Wait for it to finish, then try again.',
        action: null,
      }
    case 'no_approval':
      return {
        message: 'This approval is no longer waiting for an answer. Send a message to continue.',
        action: null,
      }
    case 'run_error':
      // The failed answer in the list carries the Retry button, so this only explains.
      return { message: notice.message, action: null }
    case 'connection_lost':
      return {
        message: 'Connection lost. The answer may still be on its way.',
        action: 'reload',
      }
    case 'failed':
      return { message: 'Something went wrong. Please try again.', action: null }
  }
}
