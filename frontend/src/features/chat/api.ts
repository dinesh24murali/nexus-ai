import { useInfiniteQuery, useQuery, type InfiniteData } from '@tanstack/react-query'
import { unwrap, useApiClient } from '../../api/client'
import type { components } from '../../api/schema'
import { useAuth } from '../../lib/auth-context'

export type Message = components['schemas']['Message']
export type Usage = components['schemas']['Usage']
type MessagePage = components['schemas']['MessagePage']

const PAGE_SIZE = 50

/** Keys carry the user id, like every other query: see frontend/AGENTS.md (State Management). */
export function useChatKeys() {
  const { user } = useAuth()
  const uid = user?.id ?? 'anonymous'
  return {
    messages: (conversationId: string) => ['messages', uid, conversationId] as const,
    usage: ['usage', uid] as const,
  }
}

/** Newest page first, as the API returns it. Use `chronological` to display. */
export function useMessages(conversationId: string) {
  const api = useApiClient()
  const keys = useChatKeys()
  return useInfiniteQuery({
    queryKey: keys.messages(conversationId),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap<MessagePage>(
        api.GET('/conversations/{conversationId}/messages', {
          params: {
            path: { conversationId },
            query: { limit: PAGE_SIZE, cursor: pageParam },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

/** Oldest first: pages arrive newest-first and each page is newest-first too. */
export function chronological(data: InfiniteData<MessagePage, unknown> | undefined) {
  return data ? data.pages.flatMap((page) => page.items).reverse() : []
}

export function useUsage() {
  const api = useApiClient()
  const keys = useChatKeys()
  return useQuery({
    queryKey: keys.usage,
    queryFn: () => unwrap<Usage>(api.GET('/me/usage')),
  })
}
