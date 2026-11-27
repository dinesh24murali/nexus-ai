import { useEffect, useState } from 'react'
import type { components } from '../../api/schema'

export type PendingInterrupt = components['schemas']['PendingInterrupt']

/**
 * What the user decided. `edit` carries the replacement query (web search) or the replacement
 * queries (a research plan); `approve` carries the chosen memory when the card is about forgetting
 * one.
 */
export type Decision =
  | { decision: 'approve'; memoryId?: string }
  | { decision: 'reject' }
  | { decision: 'edit'; query: string; queries?: undefined }
  | { decision: 'edit'; queries: string[]; query?: undefined }

export type CardProps = {
  interrupt: PendingInterrupt
  onDecide: (decision: Decision) => void
  /** A decision is on its way: the buttons wait, so one click cannot be sent twice. */
  busy?: boolean
  /** The clock, so tests can fix the time. */
  now?: () => number
}

const MAX_TIMER_MS = 2 ** 31 - 1 // setTimeout overflows above this

/**
 * True once `expiresAt` has passed, flipping at that moment without a reload. A timer cannot wait
 * longer than `MAX_TIMER_MS`, so a far deadline is waited for in several steps.
 */
export function useExpired(expiresAt: string, now: () => number): boolean {
  const deadline = new Date(expiresAt).getTime()
  const [current, setCurrent] = useState(now)
  const expired = !(deadline > current)
  useEffect(() => {
    if (expired) return
    const timer = window.setTimeout(
      () => setCurrent(now()),
      Math.min(deadline - now() + 1, MAX_TIMER_MS),
    )
    return () => clearTimeout(timer)
  }, [deadline, now, expired, current])
  return expired
}
