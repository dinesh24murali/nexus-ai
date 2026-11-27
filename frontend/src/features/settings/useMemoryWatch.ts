import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useToasts } from '../../components/toast-context'
import { useAuth } from '../../lib/auth-context'
import { useMemoryKeys, useMemoryReader, type Memory } from './memories'
import { MEMORY_CHECK_DELAYS_MS, SNAPSHOT_STALE_MS } from './timing'

const stamp = (m: Memory) => `${m.id}@${m.updated_at}`

/**
 * "Nexus remembered N thing(s)" after a turn (functional spec UF-006).
 *
 * Memories are written by a background job after the turn, so the stream cannot say whether
 * anything was stored. `begin` notes what the user has when a turn starts (from the cache when it
 * is fresh) and `finish` looks again a few times after the turn: new or changed memories become
 * one toast with a link to Settings -> Memory. Nothing happens when memory is off.
 */
export function useMemoryWatch() {
  const { user } = useAuth()
  const keys = useMemoryKeys()
  const read = useMemoryReader()
  const queryClient = useQueryClient()
  const toasts = useToasts()
  const before = useRef<Promise<Set<string> | null>>(Promise.resolve(null))
  const timers = useRef<number[]>([])
  const enabled = user?.memory_enabled ?? false

  const cancel = useCallback(() => {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }, [])
  useEffect(() => cancel, [cancel])

  /** Called as a turn starts; it does not hold the turn up. */
  const begin = useCallback(() => {
    cancel()
    if (!enabled) {
      before.current = Promise.resolve(null)
      return
    }
    before.current = queryClient
      .fetchQuery({ queryKey: keys.list, queryFn: read, staleTime: SNAPSHOT_STALE_MS })
      .then((all) => new Set(all.map(stamp)))
      .catch(() => null) // no baseline: better no toast than a wrong one
  }, [enabled, cancel, queryClient, keys.list, read])

  /** Called when the turn is over: look for what the background job stored. */
  const finish = useCallback(() => {
    if (!enabled) return
    void before.current.then((baseline) => {
      if (!baseline) return
      cancel()
      timers.current = MEMORY_CHECK_DELAYS_MS.map((delay) =>
        window.setTimeout(() => {
          void (async () => {
            try {
              const all = await queryClient.fetchQuery({
                queryKey: keys.list,
                queryFn: read,
                staleTime: 0,
              })
              const changed = all.filter((m) => !baseline.has(stamp(m)))
              if (changed.length === 0) return
              cancel()
              toasts.show({
                message: `Nexus remembered ${changed.length} ${changed.length === 1 ? 'thing' : 'things'}.`,
                action: { label: 'Manage', to: '/settings/memory' },
              })
            } catch {
              // Quiet by design: a missed toast is not worth an error.
            }
          })()
        }, delay),
      )
    })
  }, [enabled, cancel, queryClient, keys.list, read, toasts])

  return useMemo(() => ({ begin, finish, cancel }), [begin, finish, cancel])
}
