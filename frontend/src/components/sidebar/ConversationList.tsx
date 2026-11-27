import { Loader2 } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useMatch, useNavigate } from 'react-router'
import { useConversations } from '../../features/conversations/api'
import { describeProblem } from '../../lib/messages'
import { Alert } from '../ui/Alert'
import { Button } from '../ui/Button'
import { ConversationItem } from './ConversationItem'

export function ConversationList() {
  const query = useConversations()
  const navigate = useNavigate()
  const openId = useMatch('/c/:conversationId')?.params.conversationId
  const sentinel = useRef<HTMLDivElement>(null)
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query

  // Infinite scroll: load the next page when the sentinel scrolls into view. The "Load more"
  // button below is the fallback for browsers/tests without IntersectionObserver.
  useEffect(() => {
    const node = sentinel.current
    if (!node || !hasNextPage || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage()
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  if (query.isPending) {
    return (
      <div role="status" aria-label="Loading conversations" className="flex justify-center py-6">
        <Loader2 className="size-5 animate-spin text-fg-muted" aria-hidden />
      </div>
    )
  }

  if (query.isError) {
    return (
      <div className="space-y-2 p-1">
        <Alert variant="error">{describeProblem(query.error).message}</Alert>
        <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
          Try again
        </Button>
      </div>
    )
  }

  const items = query.data.pages.flatMap((p) => p.items)
  if (items.length === 0) {
    return <p className="px-3 py-4 text-body-sm text-fg-muted">No conversations yet.</p>
  }

  return (
    <>
      <ul aria-label="Conversations" className="space-y-0.5">
        {items.map((c) => (
          <ConversationItem
            key={c.id}
            conversation={c}
            active={c.id === openId}
            onDeleted={(id) => {
              if (id === openId) void navigate('/', { replace: true })
            }}
          />
        ))}
      </ul>
      {hasNextPage && (
        <div ref={sentinel} className="flex justify-center py-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void fetchNextPage()}
            loading={isFetchingNextPage}
          >
            Load more
          </Button>
        </div>
      )}
    </>
  )
}
