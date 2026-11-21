import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AuthManager } from '../../lib/auth'
import { AuthContext } from '../../lib/auth-context'
import type { Message } from './api'
import { MessageList } from './MessageList'
import type { ChatRun } from './useChatRun'

const CID = '00000000-0000-4000-8000-000000000001'
const msg = (id: string, role: Message['role'], content: string): Message => ({
  id,
  conversation_id: CID,
  role,
  content,
  status: 'complete',
  finish_reason: role === 'assistant' ? 'stop' : null,
  created_at: '2026-11-21T10:00:00Z',
})

const running = (over: Partial<ChatRun> = {}): ChatRun => ({
  running: true,
  stopping: false,
  pendingUser: null,
  streamingText: '',
  userMessageId: null,
  assistantMessageId: null,
  replacesMessageId: null,
  notice: null,
  ...over,
})

// The rating buttons call the API, so even this presentational test needs the providers (nothing
// is sent: no test here presses a thumb).
const manager = new AuthManager({ fetch: vi.fn() as unknown as typeof fetch, locks: null })

function show(messages: Message[], run: ChatRun) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthContext.Provider value={manager}>
        <MessageList
          messages={messages}
          run={run}
          hasEarlier={false}
          loadingEarlier={false}
          onLoadEarlier={() => undefined}
          onRegenerate={() => undefined}
        />
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return within(screen.getByRole('log'))
}

describe('MessageList while a run is live', () => {
  it('shows the live user text and answer until the server has stored them', () => {
    const log = show(
      [],
      running({
        pendingUser: 'hi',
        streamingText: 'Hel',
        userMessageId: 'u1',
        assistantMessageId: 'a1',
      }),
    )
    expect(log.getAllByRole('article').map((a) => a.textContent)).toEqual(['hi', 'Hel'])
  })

  it('hands over to the stored messages in one step, never showing both', () => {
    // The stored rows have landed but the run has not been marked finished yet.
    const log = show(
      [msg('u1', 'user', 'hi'), msg('a1', 'assistant', 'Hello there')],
      running({
        pendingUser: 'hi',
        streamingText: 'Hello th', // the live text may lag behind: it must not be shown
        userMessageId: 'u1',
        assistantMessageId: 'a1',
      }),
    )

    const articles = log.getAllByRole('article')
    expect(articles.map((a) => a.textContent)).toEqual(['hi', 'Hello there'])
  })

  it('hides the answer being regenerated, and only that one', () => {
    const log = show(
      [msg('u1', 'user', 'q'), msg('a1', 'assistant', 'Old answer')],
      running({ streamingText: 'Fresh', assistantMessageId: 'a2', replacesMessageId: 'a1' }),
    )
    expect(log.queryByText('Old answer')).toBeNull()
    expect(log.getByText('Fresh')).toBeInTheDocument()
  })

  it('shows the regenerated answer as soon as it is stored, with no live copy', () => {
    // The server swapped the row: a1 is gone, a2 is stored; the run is still marked live.
    const log = show(
      [msg('u1', 'user', 'q'), msg('a2', 'assistant', 'Fresh and complete')],
      running({ streamingText: 'Fresh', assistantMessageId: 'a2', replacesMessageId: 'a1' }),
    )
    expect(log.getAllByRole('article').map((a) => a.textContent)).toEqual([
      'q',
      'Fresh and complete',
    ])
  })
})
