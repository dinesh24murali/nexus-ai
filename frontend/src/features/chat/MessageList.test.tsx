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
  toolCalls: [],
  route: null,
  notice: null,
  ...over,
})

// The rating buttons call the API, so even this presentational test needs the providers (nothing
// is sent: no test here presses a thumb).
const manager = new AuthManager({ fetch: vi.fn() as unknown as typeof fetch, locks: null })

function show(messages: Message[], run: ChatRun, onRegenerate: () => void = () => undefined) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthContext.Provider value={manager}>
        <MessageList
          messages={messages}
          run={run}
          hasEarlier={false}
          loadingEarlier={false}
          onLoadEarlier={() => undefined}
          onRegenerate={onRegenerate}
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

describe('MessageList tool calls', () => {
  type Stored = NonNullable<Message['tool_calls']>[number]
  const call = (over: Partial<Stored> = {}): Stored => ({
    id: 'call_1',
    name: 'web_search',
    args: { query: 'Go release notes' },
    status: 'completed',
    summary: '2 results found',
    ...over,
  })

  it('shows the stored calls above the answer they belong to', () => {
    const stored: Message = {
      ...msg('a1', 'assistant', 'It focuses on tooling [1].'),
      tool_calls: [call(), call({ id: 'call_2', name: 'calculator', args: { expression: '2+2' } })],
    }
    const log = show([msg('u1', 'user', 'news?'), stored], running({ running: false }))

    const reply = log.getByRole('article', { name: 'Nexus reply' })
    const cards = within(within(reply).getByRole('list', { name: 'Tool calls' })).getAllByRole(
      'group',
    )
    expect(cards.map((c) => c.getAttribute('aria-label'))).toEqual([
      'Searched the web',
      'Calculated',
    ])
    expect(within(reply).getByText('Go release notes')).toBeInTheDocument()
    // Cards come first, the answer after them.
    expect(reply.textContent?.indexOf('Searched the web')).toBeLessThan(
      reply.textContent?.indexOf('It focuses') ?? -1,
    )
  })

  it('shows no list for an answer that used no tools', () => {
    const log = show(
      [msg('u1', 'user', 'hi'), msg('a1', 'assistant', 'Hello')],
      running({ running: false }),
    )
    expect(log.queryByRole('list', { name: 'Tool calls' })).toBeNull()
  })

  it('shows the live calls while the answer is being made', () => {
    const log = show(
      [],
      running({
        pendingUser: 'news?',
        toolCalls: [{ ...call(), status: 'running', summary: undefined }],
      }),
    )
    expect(log.getByRole('group', { name: 'Searching the web' })).toBeInTheDocument()
  })

  it('shows the calls once, from the stored message, when the run hands over', () => {
    const stored: Message = {
      ...msg('a1', 'assistant', 'Done'),
      tool_calls: [call()],
    }
    const log = show(
      [msg('u1', 'user', 'news?'), stored],
      running({ userMessageId: 'u1', assistantMessageId: 'a1', toolCalls: [call()] }),
    )
    expect(
      log.getByRole('list', { name: 'Tool calls' }).querySelectorAll('[role="group"]'),
    ).toHaveLength(1)
  })
})

describe('MessageList mode chip and Regenerate', () => {
  const idle = running({ running: false })
  const answer = (over: Partial<Message>): Message => ({
    ...msg('a1', 'assistant', 'Answer'),
    ...over,
  })

  it('shows a chip for a pipeline answer and none for a chat answer', () => {
    const log = show(
      [msg('u1', 'user', '/pdf'), answer({ intent: 'generate_document', route_source: 'command' })],
      idle,
    )
    expect(log.getByText('Document')).toBeInTheDocument()
    expect(log.getByTitle('chosen by your command')).toBeInTheDocument()
  })

  it('shows no chip on a chat answer', () => {
    const log = show(
      [msg('u1', 'user', 'hi'), answer({ intent: 'general_chat', route_source: 'router' })],
      idle,
    )
    expect(log.queryByText('Document')).toBeNull()
    expect(log.queryByText('Memory')).toBeNull()
    expect(log.queryByText(/Answered by/)).toBeNull()
  })

  it.each([
    ['a chat answer', { intent: 'general_chat', route_source: 'router' }, true],
    ['an answer from before routing', {}, true],
    ['a memory answer', { intent: 'manage_memory', route_source: 'router' }, false],
    ['a document answer', { intent: 'generate_document', route_source: 'command' }, false],
    ['a research answer', { intent: 'deep_research', route_source: 'router' }, false],
    ['a /help reply', { intent: null, route_source: 'command' }, false],
  ] as const)('%s: Regenerate shown = %s', (_name, fields, shown) => {
    const log = show([msg('u1', 'user', 'q'), answer(fields as Partial<Message>)], idle)
    expect(log.queryByRole('button', { name: 'Regenerate' }) !== null).toBe(shown)
  })

  it('still offers Retry for a failed chat answer, never for a failed document', () => {
    const failedChat = show(
      [msg('u1', 'user', 'q'), answer({ status: 'failed', intent: null, content: '' })],
      idle,
    )
    expect(failedChat.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('shows the chip on the live answer from the route event, before the answer is stored', () => {
    const log = show(
      [],
      running({
        pendingUser: '/memory',
        streamingText: 'Here is',
        route: { intent: 'manage_memory', source: 'command' },
      }),
    )
    expect(log.getByText('Memory')).toBeInTheDocument()
    expect(log.queryByRole('button', { name: 'Regenerate' })).toBeNull()
  })

  it('shows the chip once when the run hands over to the stored answer', () => {
    const log = show(
      [msg('u1', 'user', '/pdf'), answer({ intent: 'generate_document', route_source: 'command' })],
      running({
        userMessageId: 'u1',
        assistantMessageId: 'a1',
        route: { intent: 'generate_document', source: 'command' },
      }),
    )
    expect(log.getAllByText('Document')).toHaveLength(1)
  })
})
