import { useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { unwrap, useApiClient } from '../../api/client'
import type { components } from '../../api/schema'
import { useChatKeys, type Message } from './api'

export type Rating = -1 | 1
type MessagePage = components['schemas']['MessagePage']
type History = InfiniteData<MessagePage, unknown>

/** Copy of the cached history with one message's rating replaced. */
export function withRating(
  data: History | undefined,
  messageId: string,
  rating: Rating | null,
): History | undefined {
  if (!data) return data
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      items: page.items.map((m) => (m.id === messageId ? { ...m, feedback: rating } : m)),
    })),
  }
}

/** Rate an answer. The rating shows at once and goes back if the server refuses it. */
export function useRate(conversationId: string) {
  const api = useApiClient()
  const queryClient = useQueryClient()
  const key = useChatKeys().messages(conversationId)
  return useMutation({
    mutationFn: ({ message, rating }: { message: Message; rating: Rating }) =>
      unwrap<components['schemas']['Feedback']>(
        api.PUT('/messages/{messageId}/feedback', {
          params: { path: { messageId: message.id } },
          body: { score: rating },
        }),
      ),
    onMutate: async ({ message, rating }) => {
      await queryClient.cancelQueries({ queryKey: key })
      queryClient.setQueryData<History>(key, (data) => withRating(data, message.id, rating))
      return { before: message.feedback ?? null }
    },
    onError: (_error, { message }, context) => {
      queryClient.setQueryData<History>(key, (data) =>
        withRating(data, message.id, context?.before ?? null),
      )
    },
  })
}
