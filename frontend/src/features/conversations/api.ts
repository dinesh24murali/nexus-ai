import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query'
import { unwrap, useApiClient } from '../../api/client'
import type { components } from '../../api/schema'
import { parseProblem } from '../../lib/auth'
import { useAuth } from '../../lib/auth-context'

export type Conversation = components['schemas']['Conversation']
type Page = components['schemas']['ConversationPage']

const PAGE_SIZE = 30

/** Keys carry the user id: a cache can never be shown to a different account on the same tab. */
export function useConversationKeys() {
  const { user } = useAuth()
  const uid = user?.id ?? 'anonymous'
  return {
    list: ['conversations', uid, 'list'] as const,
    one: (id: string) => ['conversations', uid, 'one', id] as const,
  }
}

export function useConversations() {
  const api = useApiClient()
  const keys = useConversationKeys()
  return useInfiniteQuery({
    queryKey: keys.list,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap<Page>(
        api.GET('/conversations', {
          params: { query: { limit: PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

export function useConversation(id: string | undefined) {
  const api = useApiClient()
  const keys = useConversationKeys()
  return useQuery({
    queryKey: keys.one(id ?? ''),
    enabled: id !== undefined,
    retry: false, // a 404 is an answer, not a transient failure
    queryFn: () =>
      unwrap<Conversation>(
        api.GET('/conversations/{conversationId}', {
          params: { path: { conversationId: id! } },
        }),
      ),
  })
}

export function useCreateConversation() {
  const api = useApiClient()
  const keys = useConversationKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap<Conversation>(api.POST('/conversations', { body: {} })),
    onSuccess: (created) => {
      qc.setQueryData(keys.one(created.id), created)
      return qc.invalidateQueries({ queryKey: keys.list })
    },
  })
}

export function useRenameConversation() {
  const api = useApiClient()
  const keys = useConversationKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      unwrap<Conversation>(
        api.PATCH('/conversations/{conversationId}', {
          params: { path: { conversationId: id } },
          body: { title },
        }),
      ),
    onSuccess: (updated) => {
      qc.setQueryData(keys.one(updated.id), updated)
      // A rename is not activity: the server keeps `updated_at`, so patch in place (no reorder).
      qc.setQueryData<InfiniteData<Page, string | undefined>>(
        keys.list,
        (data) =>
          data && {
            ...data,
            pages: data.pages.map((p) => ({
              ...p,
              items: p.items.map((c) => (c.id === updated.id ? updated : c)),
            })),
          },
      )
    },
  })
}

export function useDeleteConversation() {
  const api = useApiClient()
  const keys = useConversationKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE('/conversations/{conversationId}', {
        params: { path: { conversationId: id } },
      })
      if (!response.ok || error !== undefined) {
        throw await parseProblem(response)
      }
    },
    onSuccess: async (_void, id) => {
      qc.removeQueries({ queryKey: keys.one(id) })
      await qc.invalidateQueries({ queryKey: keys.list })
    },
  })
}
