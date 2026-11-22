import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  answerStream,
  done,
  json,
  liveStream,
  problem,
  renderApp,
  runStarted,
  seen,
  signedIn,
  sseResponse,
  token,
  usageEvent,
  type Call,
  type Handler,
} from '../../test-utils'

const CID = '00000000-0000-4000-8000-000000000001'

type Msg = {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  status: 'complete' | 'cancelled' | 'failed'
  finish_reason?: string | null
  feedback?: -1 | 1 | null
  intent?: 'general_chat' | 'manage_memory' | 'generate_document' | 'deep_research' | null
  route_source?: 'router' | 'command' | 'fallback' | null
  created_at: string
}

let seq = 0
const msg = (role: Msg['role'], content: string, extra: Partial<Msg> = {}): Msg => ({
  id: `00000000-0000-4000-9000-${String(++seq).padStart(12, '0')}`,
  conversation_id: CID,
  role,
  content,
  status: 'complete',
  finish_reason: role === 'assistant' ? 'stop' : null,
  created_at: '2026-11-21T10:00:00Z',
  ...extra,
})

type Pending = {
  interrupt_id: string
  tool: 'web_search' | 'research_plan' | 'forget_memory'
  args: Record<string, unknown>
  created_at: string
  expires_at: string
}

const conversation = (title = 'Planning', pending: Pending | null = null) => ({
  id: CID,
  title,
  has_active_run: false,
  pending_interrupt: pending,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
})

const usage = (spend = 0.142) => ({
  day: '2026-11-21',
  spend_usd: spend,
  budget_usd: 0.5,
  tokens_used: 41230,
  messages: 18,
  global_cap_reached: false,
  resets_at: '2026-11-22T00:00:00Z',
})

type Server = {
  /** Chronological, as stored. */
  messages: Msg[]
  /** What POST …/messages answers; default: a short complete answer that is also stored. */
  onSend?: (call: Call) => Response | Promise<Response>
  onRegenerate?: (call: Call) => Response | Promise<Response>
  /** What PUT …/feedback answers; default: store the rating on the message. */
  onRate?: (call: Call) => Response | Promise<Response>
  /** The approval the conversation is waiting on (GET …/{id} reports it). */
  pending?: Pending | null
  /** What POST …/runs/resume answers; default: a complete answer, the approval is cleared. */
  onResume?: (call: Call) => Response | Promise<Response>
  spend: number
  calls: Call[]
  /** While set, reading the history waits for it (a slow server). */
  gate?: Promise<void>
}

/** A stateful fake of the chat API; `signedIn` supplies login and the (empty) sidebar list. */
function chatServer(seed: Partial<Server> = {}) {
  const server: Server = { messages: [], spend: 0.142, calls: [], ...seed }
  const handler: Handler = signedIn((call) => {
    server.calls.push(call)
    const path = new URL(call.path, 'http://localhost').pathname.replace('/api/v1', '')
    if (path === '/me/usage') return json(200, usage(server.spend))
    if (path === `/conversations/${CID}` && call.method === 'GET') {
      return json(200, conversation('Planning', server.pending ?? null))
    }
    if (path === `/conversations/${CID}/runs/resume`) {
      if (server.onResume) return server.onResume(call)
      server.pending = null
      server.messages.push(msg('assistant', 'Found it on the web [1]'))
      return answerStream('Found it on the web [1]')
    }
    if (path === `/conversations/${CID}/messages` && call.method === 'GET') {
      const reply = () => json(200, { items: [...server.messages].reverse(), next_cursor: null })
      return server.gate ? server.gate.then(reply) : reply()
    }
    if (path === `/conversations/${CID}/messages` && call.method === 'POST') {
      if (server.onSend) return server.onSend(call)
      const text = (call.body as { content: string }).content
      server.messages.push(msg('user', text), msg('assistant', `Echo: ${text}`))
      return answerStream(`Echo: ${text}`)
    }
    if (path === `/conversations/${CID}/regenerate`) {
      if (server.onRegenerate) return server.onRegenerate(call)
      server.messages.pop()
      server.messages.push(msg('assistant', 'A new answer'))
      return answerStream('A new answer')
    }
    if (path === `/conversations/${CID}/runs/cancel`) return new Response(null, { status: 202 })
    const rating = /^\/messages\/([^/]+)\/feedback$/.exec(path)
    if (rating && call.method === 'PUT') {
      if (server.onRate) return server.onRate(call)
      const target = server.messages.find((m) => m.id === rating[1])
      if (!target) return problem(404, 'NOT_FOUND')
      target.feedback = (call.body as { score: -1 | 1 }).score
      return json(200, {
        message_id: target.id,
        score: target.feedback,
        comment: null,
        created_at: '2026-11-21T10:05:00Z',
      })
    }
    return problem(500, 'INTERNAL_ERROR', `unscripted ${call.method} ${path}`)
  })
  return { server, handler }
}

const posts = (server: Server, suffix: string) =>
  server.calls.filter((c) => c.method === 'POST' && c.path.endsWith(suffix))

async function open(seed: Partial<Server> = {}) {
  const { server, handler } = chatServer(seed)
  const app = renderApp(`/c/${CID}`, handler)
  await screen.findByRole('heading', { level: 1, name: 'Planning' })
  const box = () => screen.getByRole('textbox', { name: 'Message' })
  return { ...app, server, box }
}

beforeEach(() => {
  seq = 0
})

describe('history', () => {
  it('shows the stored messages oldest first, with the answer rendered as markdown', async () => {
    await open({
      messages: [msg('user', 'Name a fruit'), msg('assistant', 'A **banana** is one.')],
    })

    const log = await screen.findByRole('log', { name: 'Conversation' })
    const articles = within(log).getAllByRole('article')
    expect(articles.map((a) => a.getAttribute('aria-label'))).toEqual([
      'Your message',
      'Nexus reply',
    ])
    expect(within(articles[0]!).getByText('Name a fruit')).toBeInTheDocument()
    expect(within(articles[1]!).getByText('banana').tagName).toBe('STRONG')
  })

  it('invites the first message when the conversation is empty', async () => {
    await open()
    await seen('Ask anything to get started.')
  })

  it('labels a stopped answer and an interrupted one, and offers Retry only on the latest', async () => {
    const server = chatServer({
      messages: [
        msg('user', 'one'),
        msg('assistant', 'Half an ans', { status: 'cancelled', finish_reason: 'cancelled' }),
        msg('user', 'two'),
        msg('assistant', '', { status: 'failed', finish_reason: 'server_restart' }),
      ],
    })
    renderApp(`/c/${CID}`, server.handler)

    await seen('Stopped')
    expect(screen.getByText(/Interrupted: the server restarted/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /retry|regenerate/i })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('loads earlier messages on request', async () => {
    const { handler, server } = chatServer()
    const newest = msg('assistant', 'newest')
    const older = msg('user', 'older')
    const pages: Record<string, unknown> = {
      first: { items: [newest], next_cursor: 'c1' },
      c1: { items: [older], next_cursor: null },
    }
    renderApp(`/c/${CID}`, (call) => {
      const url = new URL(call.path, 'http://localhost')
      if (url.pathname.endsWith(`/conversations/${CID}/messages`) && call.method === 'GET') {
        return json(200, pages[url.searchParams.get('cursor') ?? 'first'])
      }
      return handler(call)
    })
    void server

    await seen('newest')
    expect(screen.queryByText('older')).toBeNull()
    const load = screen.getByRole('button', { name: 'Load earlier messages' })
    load.click()

    await seen('older')
    const order = within(screen.getByRole('log'))
      .getAllByRole('article')
      .map((a) => a.textContent)
    expect(order).toEqual([expect.stringContaining('older'), expect.stringContaining('newest')])
    expect(screen.queryByRole('button', { name: 'Load earlier messages' })).toBeNull()
  })
})

describe('composer', () => {
  it('sends on Enter and clears the box; the stored reply replaces the live one', async () => {
    const { user, box, server } = await open()

    await user.type(box(), 'hello there{Enter}')

    await seen('Echo: hello there')
    expect(box()).toHaveValue('')
    expect(posts(server, '/messages')).toHaveLength(1)
    expect(posts(server, '/messages')[0]!.body).toEqual({ content: 'hello there' })
    expect(screen.getByText('hello there')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull())
    // exactly one reply and one user message: the live view was handed over, not duplicated
    expect(within(screen.getByRole('log')).getAllByRole('article')).toHaveLength(2)
  })

  it('adds a new line on Shift+Enter instead of sending', async () => {
    const { user, box, server } = await open()

    await user.type(box(), 'line one{Shift>}{Enter}{/Shift}line two')

    expect(box()).toHaveValue('line one\nline two')
    expect(posts(server, '/messages')).toHaveLength(0)
  })

  it('cannot send an empty or blank message', async () => {
    const { user, box, server } = await open()
    const send = screen.getByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()

    await user.type(box(), '   {Enter}')

    expect(send).toBeDisabled()
    expect(posts(server, '/messages')).toHaveLength(0)
  })

  it('limits a message to 16,000 characters', async () => {
    const { box } = await open()
    expect(box()).toHaveAttribute('maxlength', '16000')
  })

  it('has a visible name for the message box and the send button', async () => {
    const { box } = await open()
    expect(box()).toHaveAccessibleName('Message')
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
  })
})

describe('streaming', () => {
  it('shows the answer as it arrives, with Stop while it runs', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })

    await user.type(box(), 'tell me{Enter}')
    live.push(runStarted())

    await seen('tell me') // the user's own bubble, at once
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
    live.push(token('Once '))
    await seen('Once')
    live.push(token('upon a time'))
    await seen(/Once upon a time/)

    server.messages.push(msg('user', 'tell me'), msg('assistant', 'Once upon a time'))
    live.push(usageEvent())
    live.push(done())
    live.end()

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull())
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
    expect(screen.getAllByText('Once upon a time')).toHaveLength(1)
  })

  it('ignores pings between events', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })
    await user.type(box(), 'hi{Enter}')
    live.push(runStarted())
    live.ping()
    live.push(token('ok'))
    live.ping()
    await seen('ok')
    server.messages.push(msg('user', 'hi'), msg('assistant', 'ok'))
    live.push(done())
    live.end()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull())
  })

  it('shows a tool card that is running, then settled, and the stored one afterwards', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })
    await user.type(box(), 'news?{Enter}')
    live.push(runStarted())

    live.push({
      type: 'tool_start',
      call_id: 'call_1',
      name: 'web_search',
      args: { query: 'Go release notes' },
    })
    expect(await screen.findByRole('group', { name: 'Searching the web' })).toHaveTextContent(
      'Go release notes',
    )

    live.push({
      type: 'tool_end',
      call_id: 'call_1',
      name: 'web_search',
      status: 'completed',
      summary: '2 results found',
    })
    expect(await screen.findByRole('group', { name: 'Searched the web' })).toHaveTextContent(
      '2 results found',
    )
    expect(screen.queryByRole('group', { name: 'Searching the web' })).toBeNull()

    live.push(token('It focuses on tooling.'))
    await seen('It focuses on tooling.')
    const stored = {
      ...msg('assistant', 'It focuses on tooling.'),
      tool_calls: [
        {
          id: 'call_1',
          name: 'web_search' as const,
          args: { query: 'Go release notes' },
          status: 'completed' as const,
          summary: '2 results found',
        },
      ],
    }
    server.messages.push(msg('user', 'news?'), stored)
    live.push(done())
    live.end()

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull())
    expect(screen.getAllByRole('group', { name: 'Searched the web' })).toHaveLength(1)
  })

  it('shows a declined call as declined, and a call whose start was missed', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })
    await user.type(box(), 'news?{Enter}')
    live.push(runStarted())

    live.push({
      type: 'tool_end',
      call_id: 'call_9',
      name: 'web_search',
      status: 'rejected',
      summary: 'Declined by you',
    })

    expect(await screen.findByRole('group', { name: 'Web search declined' })).toHaveTextContent(
      'Declined by you',
    )
    server.messages.push(msg('user', 'news?'), msg('assistant', 'ok'))
    live.push(done())
    live.end()
  })

  it('does not allow a second send while one is running', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })
    await user.type(box(), 'first{Enter}')
    live.push(runStarted())
    await screen.findByRole('button', { name: 'Stop' })

    await user.type(box(), 'second{Enter}')

    expect(posts(server, '/messages')).toHaveLength(1)
    expect(box()).toHaveValue('second') // kept as a draft
    server.messages.push(msg('user', 'first'), msg('assistant', 'x'))
    live.push(done())
    live.end()
  })

  it('asks the server to cancel on Stop, and keeps the partial answer it saved', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })
    await user.type(box(), 'long one{Enter}')
    live.push(runStarted())
    live.push(token('Part one '))
    await screen.findByText(/Part one/)

    await user.click(screen.getByRole('button', { name: 'Stop' }))

    await waitFor(() => expect(posts(server, '/runs/cancel')).toHaveLength(1))
    // the stop is a request to the API; the stream ends when the server says so
    server.messages.push(
      msg('user', 'long one'),
      msg('assistant', 'Part one ', { status: 'cancelled', finish_reason: 'cancelled' }),
    )
    live.push(usageEvent())
    live.push(done('cancelled'))
    live.end()
    await seen('Stopped')
    expect(screen.getByText(/Part one/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument())
  })
})

describe('the end of a run', () => {
  it('shows the whole answer while the stored copy is still being read', async () => {
    const { user, box, server } = await open()
    let release!: () => void
    server.gate = new Promise<void>((resolve) => (release = resolve))

    await user.type(box(), 'alpha{Enter}')

    // The stream is over and the history read is pending: the last words must already be shown.
    await seen('Echo: alpha')
    release()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument())
    expect(within(screen.getByRole('log')).getAllByRole('article')).toHaveLength(2)
  })
})

describe('regenerate', () => {
  it('replaces the latest answer: the old one is hidden while the new one streams', async () => {
    const live = liveStream()
    const { user, server } = await open({
      messages: [msg('user', 'q'), msg('assistant', 'Old answer')],
      onRegenerate: () => live.response,
    })
    await user.click(await screen.findByRole('button', { name: 'Regenerate' }))

    live.push(runStarted())
    live.push(token('Fresh'))
    await seen('Fresh')
    expect(screen.queryByText('Old answer')).toBeNull()
    expect(posts(server, '/regenerate')).toHaveLength(1)
    expect(posts(server, '/regenerate')[0]!.body).toBeUndefined()

    server.messages.pop()
    server.messages.push(msg('assistant', 'Fresh'))
    live.push(done())
    live.end()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument(),
    )
    expect(screen.getByText('Fresh')).toBeInTheDocument()
    expect(screen.queryByText('Old answer')).toBeNull()
  })

  it('offers Regenerate only on the latest assistant answer, and not while running', async () => {
    await open({
      messages: [
        msg('user', 'a'),
        msg('assistant', 'first'),
        msg('user', 'b'),
        msg('assistant', 'second'),
      ],
    })
    expect(await screen.findAllByRole('button', { name: 'Regenerate' })).toHaveLength(1)
  })

  it('Retry on a failed answer regenerates it', async () => {
    const { user, server } = await open({
      messages: [
        msg('user', 'q'),
        msg('assistant', '', { status: 'failed', finish_reason: 'error' }),
      ],
    })

    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(posts(server, '/regenerate')).toHaveLength(1))
    await seen('A new answer')
  })
})

describe('rating an answer', () => {
  const rates = (server: Server) => server.calls.filter((c) => c.method === 'PUT')
  const thumbs = () => ({
    up: screen.getByRole('button', { name: 'Good answer' }),
    down: screen.getByRole('button', { name: 'Bad answer' }),
  })

  it('shows both thumbs unpressed on an answer nobody rated', async () => {
    await open({ messages: [msg('user', 'q'), msg('assistant', 'a')] })
    await seen('a')
    expect(thumbs().up).toHaveAttribute('aria-pressed', 'false')
    expect(thumbs().down).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('group', { name: 'Rate this answer' })).toBeInTheDocument()
  })

  it('shows a rating the server already holds', async () => {
    await open({ messages: [msg('user', 'q'), msg('assistant', 'a', { feedback: -1 })] })
    await seen('a')
    expect(thumbs().down).toHaveAttribute('aria-pressed', 'true')
    expect(thumbs().up).toHaveAttribute('aria-pressed', 'false')
  })

  it('sends the rating of the thumb that was pressed and shows it', async () => {
    const answer = msg('assistant', 'a')
    const { user, server } = await open({ messages: [msg('user', 'q'), answer] })
    await seen('a')

    await user.click(thumbs().up)

    await waitFor(() => expect(thumbs().up).toHaveAttribute('aria-pressed', 'true'))
    expect(rates(server)).toHaveLength(1)
    expect(rates(server)[0]!.path).toContain(`/messages/${answer.id}/feedback`)
    expect(rates(server)[0]!.body).toEqual({ score: 1 })
  })

  it('changes the rating when the other thumb is pressed, and ignores a repeat press', async () => {
    const { user, server } = await open({
      messages: [msg('user', 'q'), msg('assistant', 'a', { feedback: 1 })],
    })
    await seen('a')

    await user.click(thumbs().up) // already chosen: nothing to send
    expect(rates(server)).toHaveLength(0)

    await user.click(thumbs().down)
    await waitFor(() => expect(thumbs().down).toHaveAttribute('aria-pressed', 'true'))
    expect(thumbs().up).toHaveAttribute('aria-pressed', 'false')
    expect(rates(server).map((c) => c.body)).toEqual([{ score: -1 }])
  })

  it('shows the rating at once and puts it back, with the reason, when the server refuses', async () => {
    let refuse: () => void = () => undefined
    const held = new Promise<void>((resolve) => (refuse = resolve))
    const { user } = await open({
      messages: [msg('user', 'q'), msg('assistant', 'a')],
      onRate: () => held.then(() => problem(500, 'INTERNAL_ERROR', 'Something went wrong.')),
    })
    await seen('a')

    await user.click(thumbs().down)
    expect(thumbs().down).toHaveAttribute('aria-pressed', 'true') // before the server answered

    refuse()
    await screen.findByRole('alert')
    expect(thumbs().down).toHaveAttribute('aria-pressed', 'false')
    expect(thumbs().up).not.toBeDisabled()
  })

  it('does not offer a rating on a failed or empty answer, or on your own message', async () => {
    await open({
      messages: [
        msg('user', 'q'),
        msg('assistant', '', { status: 'failed', finish_reason: 'server_restart' }),
      ],
    })
    await seen('Interrupted: the server restarted while answering.')
    expect(screen.queryByRole('group', { name: 'Rate this answer' })).toBeNull()
  })

  it('keeps the rating after a regenerate replaced the answer with an unrated one', async () => {
    const { user, server } = await open({
      messages: [msg('user', 'q'), msg('assistant', 'old', { feedback: 1 })],
    })
    await seen('old')

    await user.click(screen.getByRole('button', { name: 'Regenerate' }))

    await seen('A new answer')
    await waitFor(() => expect(thumbs().up).toHaveAttribute('aria-pressed', 'false'))
    expect(rates(server)).toHaveLength(0)
  })
})

describe('refusals before the stream opens', () => {
  async function refused(response: Response) {
    const ctx = await open({ onSend: () => response })
    await ctx.user.type(ctx.box(), 'my draft{Enter}')
    return ctx
  }

  it('tells the user a flagged message was not sent and keeps their text', async () => {
    const { box } = await refused(problem(400, 'MODERATION_FLAGGED'))

    expect(await screen.findByRole('alert')).toHaveTextContent(/may violate the content policy/)
    expect(box()).toHaveValue('my draft')
    expect(screen.queryByText('my draft', { selector: 'div' })).toBeNull() // no stray user bubble
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })

  it('words the personal daily limit and shows when it resets', async () => {
    await refused(
      problem(
        429,
        'BUDGET_EXCEEDED',
        'limit',
        { details: { scope: 'user', resets_at: '2026-11-22T00:00:00Z' } },
        { 'retry-after': '3600' },
      ),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "You've reached your daily usage limit. It resets at 00:00 UTC.",
    )
  })

  it('words the instance-wide limit differently', async () => {
    await refused(
      problem(
        429,
        'BUDGET_EXCEEDED',
        'limit',
        { details: { scope: 'global', resets_at: '2026-11-22T00:00:00Z' } },
        { 'retry-after': '3600' },
      ),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Nexus has reached its daily limit for everyone. It resets at 00:00 UTC.',
    )
  })

  it('says how long to wait after a rate limit', async () => {
    await refused(problem(429, 'RATE_LIMITED', 'slow down', {}, { 'retry-after': '17' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "You're sending messages too quickly. Try again in 17 seconds.",
    )
  })

  it('says Nexus is restarting on a 503 SERVICE_RESTARTING', async () => {
    await refused(problem(503, 'SERVICE_RESTARTING', 'restarting', {}, { 'retry-after': '10' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Nexus is restarting, try again in a moment.',
    )
  })

  it('explains a conflict when the conversation is already answering', async () => {
    await refused(problem(409, 'RUN_IN_PROGRESS'))
    expect(await screen.findByRole('status')).toHaveTextContent(/already answering/)
  })

  it('can be dismissed, and goes away on the next successful send', async () => {
    const responses = [problem(400, 'MODERATION_FLAGGED')]
    const ctx = await open({
      onSend: (call) => {
        const next = responses.shift()
        if (next) return next
        const text = (call.body as { content: string }).content
        ctx.server.messages.push(msg('user', text), msg('assistant', 'fine'))
        return answerStream('fine')
      },
    })
    await ctx.user.type(ctx.box(), 'bad{Enter}')
    await ctx.user.click(await screen.findByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('alert')).toBeNull()

    await ctx.user.type(ctx.box(), '{Enter}') // the text came back; send it again, now accepted

    await seen('fine')
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('trouble during a run', () => {
  it('shows the server error text and a Retry on the answer that did not finish', async () => {
    const ctx = await open({
      onSend: (call) => {
        const text = (call.body as { content: string }).content
        ctx.server.messages.push(
          msg('user', text),
          msg('assistant', 'Par', { status: 'failed', finish_reason: 'error' }),
        )
        return sseResponse([
          runStarted(),
          token('Par'),
          {
            type: 'error',
            code: 'LLM_UNAVAILABLE',
            message: 'The model is unavailable right now. Try again.',
            retryable: true,
          },
        ])
      },
    })

    await ctx.user.type(ctx.box(), 'hello{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The model is unavailable right now. Try again.',
    )
    expect(screen.getByText("This answer didn't finish.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('reports a lost connection without resending, and Reload re-reads the history', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })
    await user.type(box(), 'hello{Enter}')
    live.push(runStarted())
    live.push(token('abc'))
    await screen.findByText('abc')

    live.fail()

    expect(await screen.findByRole('alert')).toHaveTextContent('Connection lost')
    const before = server.calls.filter(
      (c) => c.path.includes('/messages') && c.method === 'GET',
    ).length
    server.messages.push(msg('user', 'hello'), msg('assistant', 'abc and the rest'))
    await user.click(screen.getByRole('button', { name: 'Reload' }))

    await seen('abc and the rest')
    expect(posts(server, '/messages')).toHaveLength(1) // never sent twice
    expect(
      server.calls.filter((c) => c.path.includes('/messages') && c.method === 'GET').length,
    ).toBeGreaterThan(before)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('usage meter', () => {
  it("shows today's spend against the budget in the sidebar and refreshes after a run", async () => {
    const { user, box, server } = await open()
    const meter = await screen.findByRole('region', { name: 'Usage today' })
    expect(meter).toHaveTextContent('$0.14 of $0.50')
    expect(within(meter).getByRole('progressbar', { name: 'Daily spend' })).toHaveAttribute(
      'aria-valuenow',
      '0.142',
    )

    server.spend = 0.2
    await user.type(box(), 'hi{Enter}')

    await waitFor(() => expect(meter).toHaveTextContent('$0.20 of $0.50'))
  })

  it('has no inline styles (the CSP forbids them)', async () => {
    const { container } = await open()
    await screen.findByRole('region', { name: 'Usage today' })
    expect(container.querySelector('[style]')).toBeNull()
  })
})

// ---------------------------------------------------------------- tool approval (TASK-023)

const PENDING: Pending = {
  interrupt_id: 'int_9a8b7c',
  tool: 'web_search',
  args: { query: 'Go 1.24 release notes' },
  created_at: '2026-11-21T10:00:00Z',
  expires_at: '2099-01-01T00:00:00Z',
}

const interruptEvent = (interrupt: Pending = PENDING) => ({ type: 'interrupt', interrupt })

/** A send that stops at an approval: the question is stored, the answer is not. */
const pausingSend =
  (server: () => Server) =>
  (call: Call): Response => {
    const text = (call.body as { content: string }).content
    server().messages.push(msg('user', text))
    server().pending = PENDING
    return sseResponse([{ ...runStarted(), user_message_id: null }, interruptEvent()])
  }

const card = () => screen.getByRole('group', { name: 'Search the web?' })

describe('tool approval', () => {
  it('stops at the approval card when the run is interrupted, and nothing is shown as an answer', async () => {
    const holder: { server?: Server } = {}
    const { server, box } = await open({ onSend: pausingSend(() => holder.server!) })
    holder.server = server
    const user = userEvent.setup()

    await user.type(box(), 'What is new in Go?{Enter}')

    await waitFor(() => expect(card()).toBeInTheDocument())
    expect(within(card()).getByText('Go 1.24 release notes')).toBeInTheDocument()
    expect(screen.getByText('What is new in Go?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument() // not running
    expect(box()).toBeEnabled()
  })

  it('brings the card back after a reload', async () => {
    await open({ messages: [msg('user', 'What is new in Go?')], pending: PENDING })

    await waitFor(() => expect(card()).toBeInTheDocument())
    expect(within(card()).getByText('Go 1.24 release notes')).toBeInTheDocument()
  })

  it('shows an approval that ran out as expired', async () => {
    await open({
      messages: [msg('user', 'What is new in Go?')],
      pending: { ...PENDING, expires_at: '2020-01-01T00:00:00Z' },
    })

    await waitFor(() => expect(card()).toBeInTheDocument())
    expect(within(card()).getByText(/expired/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('approves: posts the decision, streams the answer and removes the card', async () => {
    const { server } = await open({
      messages: [msg('user', 'What is new in Go?')],
      pending: PENDING,
    })
    const user = userEvent.setup()
    await waitFor(() => expect(card()).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Approve' }))

    await seen('Found it on the web [1]')
    expect(posts(server, '/runs/resume')[0]?.body).toEqual({
      interrupt_id: 'int_9a8b7c',
      decision: 'approve',
    })
    await waitFor(() =>
      expect(screen.queryByRole('group', { name: 'Search the web?' })).not.toBeInTheDocument(),
    )
  })

  it('forgets a memory: posts the chosen memory id and streams the confirmation', async () => {
    const { server } = await open({
      messages: [msg('user', '/memory forget works at Acme')],
      pending: {
        ...PENDING,
        tool: 'forget_memory',
        args: {
          candidates: [
            { memory_id: 'a1b2c3d4-0000-4000-8000-000000000001', content: 'User works at Acme.' },
          ],
        },
      },
    })
    const user = userEvent.setup()
    await waitFor(() =>
      expect(screen.getByRole('group', { name: 'Forget a memory?' })).toBeInTheDocument(),
    )
    expect(screen.getByText('User works at Acme.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Forget it' }))

    await seen('Found it on the web [1]')
    expect(posts(server, '/runs/resume')[0]?.body).toEqual({
      interrupt_id: 'int_9a8b7c',
      decision: 'approve',
      edited_args: { memory_id: 'a1b2c3d4-0000-4000-8000-000000000001' },
    })
  })

  it('hides the card while the resumed run streams', async () => {
    const live = liveStream()
    const { server } = await open({
      messages: [msg('user', 'What is new in Go?')],
      pending: PENDING,
      onResume: () => live.response,
    })
    const user = userEvent.setup()
    await waitFor(() => expect(card()).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Approve' }))
    live.push({ ...runStarted(), user_message_id: null })
    live.push(token('Searching… '))
    await seen('Searching…')

    expect(screen.queryByRole('group', { name: 'Search the web?' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    server.pending = null
    server.messages.push(msg('assistant', 'Searching… done'))
    live.push(usageEvent())
    live.push(done())
    live.end()
    await seen('Searching… done')
  })

  it('edits: posts the edited query', async () => {
    const { server } = await open({
      messages: [msg('user', 'What is new in Go?')],
      pending: PENDING,
    })
    const user = userEvent.setup()
    await waitFor(() => expect(card()).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const queryBox = screen.getByRole('textbox', { name: 'Search query' })
    await user.clear(queryBox)
    await user.type(queryBox, 'Go 1.24 generics')
    await user.click(screen.getByRole('button', { name: 'Search with this query' }))

    await seen('Found it on the web [1]')
    expect(posts(server, '/runs/resume')[0]?.body).toEqual({
      interrupt_id: 'int_9a8b7c',
      decision: 'edit',
      edited_args: { query: 'Go 1.24 generics' },
    })
  })

  it('rejects: posts the decision and the model answers without the search', async () => {
    const { server } = await open({
      messages: [msg('user', 'What is new in Go?')],
      pending: PENDING,
      onResume: () => {
        const text = 'I did not search, but from memory: Go 1.23 added iterators.'
        server.pending = null
        server.messages.push(msg('assistant', text))
        return answerStream(text)
      },
    })
    const user = userEvent.setup()
    await waitFor(() => expect(card()).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Reject' }))

    await seen(/I did not search/)
    expect(posts(server, '/runs/resume')[0]?.body).toEqual({
      interrupt_id: 'int_9a8b7c',
      decision: 'reject',
    })
  })

  it('explains an approval that is no longer waiting, and drops the stale card', async () => {
    const { server } = await open({
      messages: [msg('user', 'What is new in Go?')],
      pending: PENDING,
      onResume: () => {
        server.pending = null // another tab answered it
        return problem(409, 'NO_PENDING_INTERRUPT')
      },
    })
    const user = userEvent.setup()
    await waitFor(() => expect(card()).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Approve' }))

    await seen(/no longer waiting/i)
    await waitFor(() =>
      expect(screen.queryByRole('group', { name: 'Search the web?' })).not.toBeInTheDocument(),
    )
  })

  it('goes away when a new message is sent instead', async () => {
    const { server, box } = await open({
      messages: [msg('user', 'What is new in Go?')],
      pending: PENDING,
      onSend: (call) => {
        server.pending = null // the server abandons the approval
        const text = (call.body as { content: string }).content
        server.messages.push(msg('user', text), msg('assistant', `Echo: ${text}`))
        return answerStream(`Echo: ${text}`)
      },
    })
    const user = userEvent.setup()
    await waitFor(() => expect(card()).toBeInTheDocument())

    await user.type(box(), 'never mind{Enter}')

    await seen('Echo: never mind')
    await waitFor(() =>
      expect(screen.queryByRole('group', { name: 'Search the web?' })).not.toBeInTheDocument(),
    )
  })
})

describe('mode chip', () => {
  const routed = (intent: 'manage_memory' | 'generate_document', source: 'command' | 'router') =>
    ({ type: 'route_selected', intent, source, confidence: null }) as const

  it('shows the chip on the live answer, then on the stored one, once', async () => {
    const live = liveStream()
    const { user, box, server } = await open({ onSend: () => live.response })

    await user.type(box(), '/memory{Enter}')
    live.push(runStarted())
    live.push(routed('manage_memory', 'command'))
    live.push(token('Here is what I remember'))
    await seen('Memory')
    expect(screen.queryByRole('button', { name: 'Regenerate' })).toBeNull()

    server.messages.push(
      msg('user', '/memory'),
      msg('assistant', 'Here is what I remember', {
        intent: 'manage_memory',
        route_source: 'command',
      }),
    )
    live.push(usageEvent())
    live.push(done())
    live.end()

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull())
    // "Memory" is also a navigation link: count the chip inside the conversation only.
    const log = within(screen.getByRole('log', { name: 'Conversation' }))
    expect(log.getAllByText('Memory')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Regenerate' })).toBeNull()
  })

  it('shows no chip for a router that answered with chat', async () => {
    const { user, box } = await open()
    await user.type(box(), 'hello{Enter}')
    await seen('Echo: hello')
    expect(screen.queryByText(/Answered by/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument()
  })
})

describe('slash menu', () => {
  it('opens on a slash, narrows as you type and inserts the command without sending', async () => {
    const { user, box, server } = await open()

    await user.type(box(), '/')
    const menu = screen.getByRole('listbox', { name: 'Commands' })
    expect(within(menu).getAllByRole('option').length).toBeGreaterThanOrEqual(8)
    expect(box()).toHaveAttribute('aria-controls', menu.id)

    await user.type(box(), 'br')
    expect(within(menu).getAllByRole('option')).toHaveLength(1)
    await user.click(within(menu).getByRole('option', { name: /\/brief/ }))

    expect(box()).toHaveValue('/brief ')
    expect(screen.queryByRole('listbox')).toBeNull() // the command is complete: the menu is done
    expect(posts(server, '/messages')).toHaveLength(0) // nothing was sent
  })

  it('moves with the arrows and completes with Enter, which does not send', async () => {
    const { user, box, server } = await open()
    await user.type(box(), '/pd')
    expect(box()).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[0]!.id)

    await user.keyboard('{ArrowDown}')
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{Enter}')

    expect(box()).toHaveValue('/pdf last')
    expect(posts(server, '/messages')).toHaveLength(0)
  })

  it('sends a command that is already typed in full instead of answering the menu', async () => {
    const { user, box, server } = await open()
    await user.type(box(), '/pdf{Enter}')

    await waitFor(() => expect(posts(server, '/messages')).toHaveLength(1))
    expect(posts(server, '/messages')[0]!.body).toEqual({ content: '/pdf' })
  })

  it('closes on Escape until the draft changes, and Enter then sends', async () => {
    const { user, box, server } = await open()
    await user.type(box(), '/me')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()

    await user.type(box(), 'x') // `/mex` matches nothing; the menu stays closed
    expect(screen.queryByRole('listbox')).toBeNull()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(posts(server, '/messages')).toHaveLength(1))
  })

  it('does not open for a slash that is not a first word', async () => {
    const { user, box } = await open()
    await user.type(box(), 'try and/or')
    expect(screen.queryByRole('listbox')).toBeNull()
    await user.clear(box())
    await user.type(box(), '/etc/hosts')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('does not offer commands while a run is live', async () => {
    const live = liveStream()
    const { user, box } = await open({ onSend: () => live.response })
    await user.type(box(), 'go{Enter}')
    live.push(runStarted())
    await screen.findByRole('button', { name: 'Stop' })
    await user.type(box(), '/')
    expect(screen.queryByRole('listbox')).toBeNull()
    live.push(usageEvent())
    live.push(done())
    live.end()
  })
})

// ---------------------------------------------------------------- deep research (TASK-043)

const PLAN: Pending = {
  interrupt_id: 'int_plan1',
  tool: 'research_plan',
  args: { topic: 'solid-state batteries', queries: ['query one', 'query two', 'query three'] },
  created_at: '2026-11-21T10:00:00Z',
  expires_at: '2099-01-01T00:00:00Z',
}

const planCard = () => screen.getByRole('group', { name: 'Research plan' })

describe('deep research', () => {
  it('shows the plan as a card, and approving posts the decision and streams the report', async () => {
    const { server } = await open({
      messages: [msg('user', '/research solid-state batteries')],
      pending: PLAN,
      onResume: () => {
        server.pending = null
        server.messages.push(msg('assistant', 'The report [1]', { intent: 'deep_research' }))
        return sseResponse([
          { ...runStarted(), user_message_id: null },
          { type: 'progress', stage: 'searching', done: 3, total: 3 },
          token('The report [1]'),
          usageEvent(),
          done(),
        ])
      },
    })
    const user = userEvent.setup()
    await waitFor(() => expect(planCard()).toBeInTheDocument())
    expect(within(planCard()).getAllByRole('listitem')).toHaveLength(3)

    await user.click(screen.getByRole('button', { name: 'Approve and search' }))

    await seen('The report [1]')
    expect(posts(server, '/runs/resume')[0]?.body).toEqual({
      interrupt_id: 'int_plan1',
      decision: 'approve',
    })
    await waitFor(() =>
      expect(screen.queryByRole('group', { name: 'Research plan' })).not.toBeInTheDocument(),
    )
    expect(screen.getByText('Research')).toBeInTheDocument() // the mode chip
    expect(screen.queryByRole('button', { name: 'Regenerate' })).not.toBeInTheDocument()
  })

  it('posts the edited queries as edited_args.queries', async () => {
    const { server } = await open({
      messages: [msg('user', '/research solid-state batteries')],
      pending: PLAN,
      onResume: () => {
        server.pending = null
        server.messages.push(msg('assistant', 'Done', { intent: 'deep_research' }))
        return answerStream('Done')
      },
    })
    const user = userEvent.setup()
    await waitFor(() => expect(planCard()).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.clear(screen.getByRole('textbox', { name: 'Search query 1' }))
    await user.type(screen.getByRole('textbox', { name: 'Search query 1' }), 'my own query')
    await user.click(screen.getByRole('button', { name: 'Remove query 3' }))
    await user.click(screen.getByRole('button', { name: 'Search with these queries' }))

    await seen('Done')
    expect(posts(server, '/runs/resume')[0]?.body).toEqual({
      interrupt_id: 'int_plan1',
      decision: 'edit',
      edited_args: { queries: ['my own query', 'query two'] },
    })
  })

  it('rejecting posts the decision', async () => {
    const { server } = await open({
      messages: [msg('user', '/research solid-state batteries')],
      pending: PLAN,
      onResume: () => {
        server.pending = null
        server.messages.push(msg('assistant', 'Okay.', { intent: 'deep_research' }))
        return answerStream('Okay.')
      },
    })
    const user = userEvent.setup()
    await waitFor(() => expect(planCard()).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: "Don't search" }))

    await seen('Okay.')
    expect(posts(server, '/runs/resume')[0]?.body).toEqual({
      interrupt_id: 'int_plan1',
      decision: 'reject',
    })
  })

  it('says what the run is doing while it works, instead of "Thinking…"', async () => {
    const stream = liveStream()
    const { box } = await open({ onSend: () => stream.response })
    const user = userEvent.setup()

    await user.type(box(), '/research solid-state batteries{Enter}')
    stream.push(runStarted())
    await seen('Thinking…')

    stream.push({ type: 'progress', stage: 'planning', done: null, total: null })
    await seen('Planning the research…')
    stream.push({ type: 'progress', stage: 'searching', done: 2, total: 4 })
    await seen('Searching the web: 2 of 4 done…')
    stream.push({ type: 'progress', stage: 'writing', done: null, total: null })
    await seen('Writing the report…')

    stream.push(token('# Report'))
    await waitFor(() => expect(screen.queryByText('Writing the report…')).not.toBeInTheDocument())
    stream.push(done())
    stream.end()
  })
})
