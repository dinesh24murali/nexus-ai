import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { unwrap, unwrapEmpty, useApiClient } from '../../api/client'
import type { components } from '../../api/schema'
import { ApiProblem } from '../../lib/auth'
import { useAuth } from '../../lib/auth-context'
import { describeProblem } from '../../lib/messages'

export type Memory = components['schemas']['Memory']
type MemoryPage = components['schemas']['MemoryPage']
type User = components['schemas']['User']

/** The most memories a user can have (the API evicts the least recently used past this). */
export const MEMORY_CAP = 200
export const MAX_MEMORY_CHARS = 500
const PAGE = 100

/** Keys carry the user id, like every other query (frontend/AGENTS.md, State Management). */
export function useMemoryKeys() {
  const { user } = useAuth()
  const uid = user?.id ?? 'anonymous'
  return { list: ['memories', uid, 'list'] as const }
}

/** Every memory, newest first. A user has at most 200, so this is two requests at the most. */
export function useMemoryReader() {
  const api = useApiClient()
  return async (): Promise<Memory[]> => {
    const found: Memory[] = []
    let cursor: string | undefined
    do {
      const page: MemoryPage = await unwrap<MemoryPage>(
        api.GET('/memories', { params: { query: { limit: PAGE, cursor } } }),
      )
      found.push(...page.items)
      cursor = page.next_cursor ?? undefined
    } while (cursor)
    return found
  }
}

export function useMemories() {
  const keys = useMemoryKeys()
  const read = useMemoryReader()
  return useQuery({ queryKey: keys.list, queryFn: read })
}

export function useAddMemory() {
  const api = useApiClient()
  const keys = useMemoryKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (content: string) => unwrap<Memory>(api.POST('/memories', { body: { content } })),
    onSuccess: (created) => {
      qc.setQueryData<Memory[]>(keys.list, (all) => [
        created,
        ...(all ?? []).filter((m) => m.id !== created.id),
      ])
      return qc.invalidateQueries({ queryKey: keys.list }) // the cap may have evicted another
    },
  })
}

export function useEditMemory() {
  const api = useApiClient()
  const keys = useMemoryKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      unwrap<Memory>(
        api.PATCH('/memories/{memoryId}', {
          params: { path: { memoryId: id } },
          body: { content },
        }),
      ),
    onSuccess: (updated) => {
      qc.setQueryData<Memory[]>(keys.list, (all) =>
        all?.map((m) => (m.id === updated.id ? updated : m)),
      )
      return qc.invalidateQueries({ queryKey: keys.list })
    },
  })
}

export function useDeleteMemory() {
  const api = useApiClient()
  const keys = useMemoryKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrapEmpty(api.DELETE('/memories/{memoryId}', { params: { path: { memoryId: id } } })),
    onSuccess: (_void, id) => {
      qc.setQueryData<Memory[]>(keys.list, (all) => all?.filter((m) => m.id !== id))
      return qc.invalidateQueries({ queryKey: keys.list })
    },
    // Already gone (deleted elsewhere): the list should say so too.
    onError: (error) => {
      if (error instanceof ApiProblem && error.status === 404) {
        return qc.invalidateQueries({ queryKey: keys.list })
      }
    },
  })
}

export function useClearMemories() {
  const api = useApiClient()
  const keys = useMemoryKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrapEmpty(api.DELETE('/memories')),
    onSuccess: () => {
      qc.setQueryData<Memory[]>(keys.list, [])
      return qc.invalidateQueries({ queryKey: keys.list })
    },
  })
}

/** The "Use long-term memory" switch: `PATCH /me`, then the signed-in user is updated in place. */
export function useSetMemoryEnabled() {
  const api = useApiClient()
  const { manager } = useAuth()
  return useMutation({
    mutationFn: (enabled: boolean) =>
      unwrap<User>(api.PATCH('/me', { body: { memory_enabled: enabled } })),
    onSuccess: (user) => manager.updateUser(user),
  })
}

/** Friendly text for a failed memory call (functional spec FS-006). */
export function describeMemoryProblem(error: unknown): string {
  if (error instanceof ApiProblem) {
    if (error.code === 'MEMORY_DUPLICATE') return 'You already have a memory like this.'
    if (error.code === 'NOT_FOUND') return 'That memory no longer exists.'
    if (error.code === 'VALIDATION_FAILED') {
      const field = describeProblem(error).fields.content
      if (field) return field
      return `A memory needs 1 to ${MAX_MEMORY_CHARS} characters.`
    }
  }
  return describeProblem(error).message
}

export const SOURCE_LABEL: Record<Memory['source'], string> = {
  extracted: 'Learned from a chat',
  chat: 'Saved from a chat command',
  manual: 'Added by you',
}
