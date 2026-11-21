import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
  token,
  type Call,
  type Handler,
} from '../test-utils'

const NEW_ID = '00000000-0000-4000-8000-0000000000aa'

const created = {
  id: NEW_ID,
  title: 'New chat',
  has_active_run: false,
  pending_interrupt: null,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
}

const usage = {
  day: '2026-11-21',
  spend_usd: 0,
  budget_usd: 0.5,
  tokens_used: 0,
  messages: 0,
  global_cap_reached: false,
  resets_at: '2026-11-22T00:00:00Z',
}

/** The API for a brand-new account: no conversations, one gets created, the reply is "Hi back". */
function homeApi(overrides: { create?: () => Response; send?: (call: Call) => Response } = {}) {
  const stored: unknown[] = []
  const handler: Handler = signedIn((call) => {
    const path = new URL(call.path, 'http://localhost').pathname.replace('/api/v1', '')
    if (path === '/me/usage') return json(200, usage)
    if (path === '/conversations' && call.method === 'POST') {
      return overrides.create ? overrides.create() : json(201, created)
    }
    if (path === `/conversations/${NEW_ID}` && call.method === 'GET') return json(200, created)
    if (path === `/conversations/${NEW_ID}/messages` && call.method === 'GET') {
      return json(200, { items: stored, next_cursor: null })
    }
    if (path === `/conversations/${NEW_ID}/messages` && call.method === 'POST') {
      if (overrides.send) return overrides.send(call)
      const text = (call.body as { content: string }).content
      stored.unshift(
        {
          id: '00000000-0000-4000-9000-0000000000b2',
          conversation_id: NEW_ID,
          role: 'assistant',
          content: 'Hi back',
          status: 'complete',
          finish_reason: 'stop',
          created_at: '2026-11-21T10:00:01Z',
        },
        {
          id: '00000000-0000-4000-9000-0000000000b1',
          conversation_id: NEW_ID,
          role: 'user',
          content: text,
          status: 'complete',
          finish_reason: null,
          created_at: '2026-11-21T10:00:00Z',
        },
      )
      return answerStream('Hi back')
    }
    return problem(500, 'INTERNAL_ERROR', `unscripted ${call.method} ${path}`)
  })
  return handler
}

const sends = (calls: { method: string; path: string }[]) =>
  calls.filter((c) => c.method === 'POST' && c.path.endsWith('/messages'))

describe('first message from the home page', () => {
  it('creates the conversation, opens it and sends the message exactly once', async () => {
    const { user, calls } = renderApp('/', homeApi())
    const box = await screen.findByRole('textbox', { name: 'Message' })

    await user.type(box, 'Hello Nexus{Enter}')

    expect(await screen.findByRole('heading', { level: 1, name: 'New chat' })).toBeInTheDocument()
    await seen('Hi back')
    expect(screen.getByText('Hello Nexus')).toBeInTheDocument()
    expect(sends(calls)).toHaveLength(1)
    expect(sends(calls)[0]).toMatchObject({
      path: expect.stringContaining(`/conversations/${NEW_ID}/messages`),
    })
  })

  it('still sends once, and does not cut the stream, under StrictMode', async () => {
    const live = liveStream()
    const { user, calls } = renderApp('/', homeApi({ send: () => live.response }), { strict: true })
    const box = await screen.findByRole('textbox', { name: 'Message' })

    await user.type(box, 'Hello Nexus{Enter}')
    live.push(runStarted())
    await screen.findByRole('button', { name: 'Stop' })
    // StrictMode's extra mount/unmount happens right after the first render; give any wrongly
    // scheduled abort time to fire before the answer starts.
    await new Promise((r) => setTimeout(r, 50))
    live.push(token('Hi '))
    live.push(token('back'))

    await seen('Hi back')
    live.push(done())
    live.end()
    expect(sends(calls)).toHaveLength(1)
  })

  it('puts the text back in the box when the first message is refused after the hand-off', async () => {
    const { user } = renderApp('/', homeApi({ send: () => problem(400, 'MODERATION_FLAGGED') }))
    const box = await screen.findByRole('textbox', { name: 'Message' })

    await user.type(box, 'Hello Nexus{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent(/content policy/)
    expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue('Hello Nexus')
  })

  it('keeps the typed text and shows an error when the conversation cannot be created', async () => {
    const { user, calls } = renderApp(
      '/',
      homeApi({ create: () => problem(500, 'INTERNAL_ERROR') }),
    )
    const box = await screen.findByRole('textbox', { name: 'Message' })

    await user.type(box, 'Hello Nexus{Enter}')

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(box).toHaveValue('Hello Nexus')
    expect(sends(calls)).toHaveLength(0)
  })
})
