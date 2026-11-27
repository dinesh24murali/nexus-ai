import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  answerStream,
  authBody,
  json,
  problem,
  renderApp,
  USER,
  type Handler,
} from '../../test-utils'
import type { Memory } from '../settings/memories'

// Shrink the waits so a test does not sit through 1.5 s + 4 s.
vi.mock('../settings/timing', () => ({
  MEMORY_CHECK_DELAYS_MS: [20, 60, 120],
  SNAPSHOT_STALE_MS: 0,
}))
vi.mock('./timing', () => ({ TITLE_REFRESH_DELAYS_MS: [], TOKEN_BATCH_MS: 1 }))

const CID = '00000000-0000-4000-8000-000000000001'
const conversation = {
  id: CID,
  title: 'A chat',
  has_active_run: false,
  pending_interrupt: null,
  created_at: '2026-11-22T10:00:00Z',
  updated_at: '2026-11-22T10:00:00Z',
}
const memory = (n: number): Memory => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  content: `Fact ${n}.`,
  source: 'extracted',
  conversation_id: CID,
  created_at: '2026-11-22T10:00:00Z',
  updated_at: '2026-11-22T10:00:00Z',
})

/** The chat page's API, with a memories list the test controls. `learn` runs when a message is
 * sent: it is where the (fake) background job stores something, a moment after the answer. */
function chatApi(options: { memories: () => Memory[]; learn?: () => void; enabled?: boolean }) {
  const reads: string[] = []
  const handler: Handler = (call) => {
    const path = new URL(call.path, 'http://localhost').pathname.replace('/api/v1', '')
    if (path === '/auth/refresh') {
      return json(200, {
        ...authBody(),
        user: { ...USER, memory_enabled: options.enabled ?? true },
      })
    }
    if (path === '/conversations' && call.method === 'GET') {
      return json(200, { items: [], next_cursor: null })
    }
    if (path === `/conversations/${CID}` && call.method === 'GET') return json(200, conversation)
    if (path === `/conversations/${CID}/messages` && call.method === 'GET') {
      return json(200, { items: [], next_cursor: null })
    }
    if (path === `/conversations/${CID}/messages` && call.method === 'POST') {
      options.learn?.()
      return answerStream('Sure')
    }
    if (path === '/memories' && call.method === 'GET') {
      reads.push(call.path)
      return json(200, { items: options.memories(), next_cursor: null })
    }
    return problem(500, 'INTERNAL_ERROR')
  }
  return { handler, reads }
}

const send = async (
  user: ReturnType<typeof renderApp>['user'],
  text = 'Remember that I like Go',
) => {
  await user.type(await screen.findByRole('textbox', { name: 'Message' }), `${text}{Enter}`)
}

describe('"Nexus remembered" toast', () => {
  it('appears once, with a link to Settings → Memory, when the job stored something', async () => {
    let rows: Memory[] = [memory(1)]
    const api = chatApi({
      memories: () => rows,
      learn: () => setTimeout(() => (rows = [memory(2), memory(1)]), 30),
    })
    const { user } = renderApp(`/c/${CID}`, api.handler)

    await send(user)

    const toast = await screen.findByText('Nexus remembered 1 thing.')
    expect(toast).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Manage' })).toHaveAttribute('href', '/settings/memory')
    await new Promise((r) => setTimeout(r, 250))
    expect(screen.getAllByText(/Nexus remembered/)).toHaveLength(1) // checks stop at the first hit
  })

  it('counts several new memories', async () => {
    let rows: Memory[] = []
    const api = chatApi({
      memories: () => rows,
      learn: () => setTimeout(() => (rows = [memory(2), memory(1)]), 30),
    })
    const { user } = renderApp(`/c/${CID}`, api.handler)

    await send(user)

    expect(await screen.findByText('Nexus remembered 2 things.')).toBeInTheDocument()
  })

  it('also notices a memory that was changed rather than added', async () => {
    let rows: Memory[] = [memory(1)]
    const api = chatApi({
      memories: () => rows,
      learn: () =>
        setTimeout(() => (rows = [{ ...memory(1), updated_at: '2026-11-22T10:05:00Z' }]), 30),
    })
    const { user } = renderApp(`/c/${CID}`, api.handler)

    await send(user)

    expect(await screen.findByText('Nexus remembered 1 thing.')).toBeInTheDocument()
  })

  it('stays quiet when nothing was stored', async () => {
    const api = chatApi({ memories: () => [memory(1)] })
    const { user } = renderApp(`/c/${CID}`, api.handler)

    await send(user)
    await waitFor(() => expect(api.reads.length).toBeGreaterThan(2)) // it did look, a few times
    await new Promise((r) => setTimeout(r, 200))

    expect(screen.queryByText(/Nexus remembered/)).not.toBeInTheDocument()
  })

  it('does not look at all when memory is off', async () => {
    const api = chatApi({ memories: () => [memory(1)], enabled: false })
    const { user } = renderApp(`/c/${CID}`, api.handler)

    await send(user)
    await screen.findByRole('button', { name: 'Send' })
    await new Promise((r) => setTimeout(r, 200))

    expect(api.reads).toHaveLength(0)
    expect(screen.queryByText(/Nexus remembered/)).not.toBeInTheDocument()
  })

  it('does not toast for a memory that already existed before the message', async () => {
    const rows: Memory[] = [memory(1), memory(2)]
    const api = chatApi({ memories: () => rows })
    const { user } = renderApp(`/c/${CID}`, api.handler)

    await send(user)
    await new Promise((r) => setTimeout(r, 250))

    expect(screen.queryByText(/Nexus remembered/)).not.toBeInTheDocument()
  })

  it('a failing memory list costs no error, just no toast', async () => {
    const api = chatApi({
      memories: () => {
        throw new Error('boom')
      },
    })
    const handler: Handler = (call) =>
      new URL(call.path, 'http://localhost').pathname.endsWith('/memories')
        ? problem(500, 'INTERNAL_ERROR')
        : api.handler(call)
    const { user } = renderApp(`/c/${CID}`, handler)

    await send(user)
    await new Promise((r) => setTimeout(r, 200))

    expect(screen.queryByText(/Nexus remembered/)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
