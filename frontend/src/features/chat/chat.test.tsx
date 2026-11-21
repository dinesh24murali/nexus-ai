import { screen, waitFor, within } from '@testing-library/react'
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

const conversation = (title = 'Planning') => ({
  id: CID,
  title,
  has_active_run: false,
  pending_interrupt: null,
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
    if (path === `/conversations/${CID}` && call.method === 'GET') return json(200, conversation())
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
