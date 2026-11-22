import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  authBody,
  emptyPage,
  json,
  problem,
  renderApp,
  USER,
  type Call,
  type Handler,
} from '../../test-utils'
import type { Memory } from './memories'

const memory = (n: number, over: Partial<Memory> = {}): Memory => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  content: `Fact number ${n}.`,
  source: 'extracted',
  conversation_id: null,
  created_at: '2026-11-20T10:00:00Z',
  updated_at: '2026-11-20T10:00:00Z',
  ...over,
})

type Override = (call: Call) => Response | undefined

/** A small in-memory memories API, newest first, plus `PATCH /me` for the switch. */
function fakeMemories(initial: Memory[], override?: Override) {
  let rows = [...initial]
  let next = initial.length + 1
  let enabled = true
  const calls: Call[] = []
  const handler: Handler = (call) => {
    if (call.path.endsWith('/auth/refresh')) return json(200, authBody())
    const early = override?.(call)
    if (early) return early
    const { pathname } = new URL(call.path, 'http://localhost')
    if (pathname.endsWith('/conversations')) return json(200, emptyPage)
    if (pathname.endsWith('/me/usage')) return problem(500, 'INTERNAL_ERROR')
    if (pathname.endsWith('/me') && call.method === 'PATCH') {
      calls.push(call)
      enabled = (call.body as { memory_enabled: boolean }).memory_enabled
      return json(200, { ...USER, memory_enabled: enabled })
    }
    if (pathname.endsWith('/memories') && call.method === 'GET') {
      return json(200, { items: rows, next_cursor: null })
    }
    if (pathname.endsWith('/memories') && call.method === 'POST') {
      calls.push(call)
      const created = memory(next++, {
        content: (call.body as { content: string }).content,
        source: 'manual',
        created_at: '2026-11-22T10:00:00Z',
      })
      rows = [created, ...rows]
      return json(201, created)
    }
    if (pathname.endsWith('/memories') && call.method === 'DELETE') {
      calls.push(call)
      rows = []
      return new Response(null, { status: 204 })
    }
    const id = pathname.split('/memories/')[1]
    const row = rows.find((r) => r.id === id)
    if (call.method === 'PATCH') {
      calls.push(call)
      if (!row) return problem(404, 'NOT_FOUND', 'Memory not found.')
      const updated = { ...row, content: (call.body as { content: string }).content }
      rows = rows.map((r) => (r.id === id ? updated : r))
      return json(200, updated)
    }
    if (call.method === 'DELETE') {
      calls.push(call)
      if (!row) return problem(404, 'NOT_FOUND', 'Memory not found.')
      rows = rows.filter((r) => r.id !== id)
      return new Response(null, { status: 204 })
    }
    return problem(500, 'INTERNAL_ERROR')
  }
  return { handler, calls, rows: () => rows }
}

const list = () => screen.findByRole('list', { name: 'Memories' })
const rowOf = async (text: string) => {
  const items = within(await list()).getAllByRole('listitem')
  const row = items.find((li) => within(li).queryByText(text))
  if (!row) throw new Error(`no row with ${text}`)
  return row
}

describe('Settings → Memory', () => {
  it('lists the memories with where each came from, and how many of the 200 are used', async () => {
    const fake = fakeMemories([
      memory(2, { source: 'manual' }),
      memory(1, { source: 'extracted', content: 'Prefers Go.' }),
    ])
    renderApp('/settings/memory', fake.handler)

    expect(await screen.findByRole('heading', { level: 1, name: 'Memory' })).toBeInTheDocument()
    const items = within(await list()).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(within(items[0]!).getByText('Fact number 2.')).toBeInTheDocument()
    expect(within(items[0]!).getByText(/Added by you/)).toBeInTheDocument()
    expect(within(items[1]!).getByText(/Learned from a chat/)).toBeInTheDocument()
    expect(screen.getByLabelText('Memories used')).toHaveTextContent('2/200 memories')
  })

  it('says so when there is nothing yet, and offers no clearing', async () => {
    renderApp('/settings/memory', fakeMemories([]).handler)

    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear all' })).toBeDisabled()
  })

  it('shows a failed load with a way to try again', async () => {
    let failing = true
    const fake = fakeMemories([memory(1)], (call) =>
      failing && call.method === 'GET' && call.path.includes('/memories')
        ? problem(500, 'INTERNAL_ERROR')
        : undefined,
    )
    const { user } = renderApp('/settings/memory', fake.handler)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    failing = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Fact number 1.')).toBeInTheDocument()
  })

  it('is reachable from the sidebar', async () => {
    const { user } = renderApp('/files', fakeMemories([memory(1)]).handler)

    await user.click(await screen.findByRole('link', { name: 'Memory' }))

    expect(await screen.findByText('Fact number 1.')).toBeInTheDocument()
  })

  describe('the switch', () => {
    it('turns memory off for the account and back on', async () => {
      const fake = fakeMemories([memory(1)])
      const { user } = renderApp('/settings/memory', fake.handler)
      const toggle = await screen.findByRole('switch', { name: 'Use long-term memory' })
      expect(toggle).toBeChecked()

      await user.click(toggle)

      await waitFor(() => expect(toggle).not.toBeChecked())
      expect(fake.calls.at(-1)?.body).toEqual({ memory_enabled: false })
      await user.click(toggle)
      await waitFor(() => expect(toggle).toBeChecked())
      expect(fake.calls.at(-1)?.body).toEqual({ memory_enabled: true })
    })

    it('keeps its position and says why when the server refuses', async () => {
      const fake = fakeMemories([], (call) =>
        call.method === 'PATCH' ? problem(500, 'INTERNAL_ERROR') : undefined,
      )
      const { user } = renderApp('/settings/memory', fake.handler)
      const toggle = await screen.findByRole('switch', { name: 'Use long-term memory' })

      await user.click(toggle)

      expect(await screen.findByRole('alert')).toBeInTheDocument()
      expect(toggle).toBeChecked()
    })

    it('still lets you manage memories while it is off', async () => {
      const fake = fakeMemories([memory(1)])
      const { user } = renderApp('/settings/memory', fake.handler)
      await user.click(await screen.findByRole('switch', { name: 'Use long-term memory' }))

      await user.click(screen.getByRole('button', { name: /^Delete: Fact number 1/ }))
      await user.click(screen.getByRole('button', { name: /^Confirm delete/ }))

      await waitFor(() => expect(screen.queryByText('Fact number 1.')).not.toBeInTheDocument())
    })
  })

  describe('adding', () => {
    it('adds a memory at the top and clears the box', async () => {
      const fake = fakeMemories([memory(1)])
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.type(await screen.findByLabelText('Add a memory'), 'Lives in Pune.')
      await user.click(screen.getByRole('button', { name: 'Add' }))

      const items = within(await list()).getAllByRole('listitem')
      expect(within(items[0]!).getByText('Lives in Pune.')).toBeInTheDocument()
      expect(within(items[0]!).getByText(/Added by you/)).toBeInTheDocument()
      expect(screen.getByLabelText('Add a memory')).toHaveValue('')
      expect(fake.calls[0]?.body).toEqual({ content: 'Lives in Pune.' })
    })

    it('cannot add nothing', async () => {
      renderApp('/settings/memory', fakeMemories([]).handler)

      expect(await screen.findByRole('button', { name: 'Add' })).toBeDisabled()
    })

    it('says when you already have almost the same memory, and keeps what you typed', async () => {
      const fake = fakeMemories([], (call) =>
        call.method === 'POST' ? problem(409, 'MEMORY_DUPLICATE') : undefined,
      )
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.type(await screen.findByLabelText('Add a memory'), 'Prefers Go.')
      await user.click(screen.getByRole('button', { name: 'Add' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'You already have a memory like this.',
      )
      expect(screen.getByLabelText('Add a memory')).toHaveValue('Prefers Go.')
    })

    it("shows the server's reason when a text may not be remembered", async () => {
      const reason = 'Nexus does not remember this kind of personal information.'
      const fake = fakeMemories([], (call) =>
        call.method === 'POST'
          ? problem(422, 'VALIDATION_FAILED', 'The request body is invalid.', {
              details: { fields: [{ loc: ['body', 'content'], msg: reason, type: 'value_error' }] },
            })
          : undefined,
      )
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.type(await screen.findByLabelText('Add a memory'), 'I have asthma')
      await user.click(screen.getByRole('button', { name: 'Add' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(reason)
    })
  })

  describe('editing inline', () => {
    it('saves the new text', async () => {
      const fake = fakeMemories([memory(1, { content: 'Prefers Go.' })])
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.click(await screen.findByRole('button', { name: 'Edit: Prefers Go.' }))
      const box = screen.getByLabelText('Memory text')
      expect(box).toHaveValue('Prefers Go.')
      await user.clear(box)
      await user.type(box, 'Prefers Rust now.')
      await user.click(screen.getByRole('button', { name: 'Save' }))

      expect(await screen.findByText('Prefers Rust now.')).toBeInTheDocument()
      expect(screen.queryByLabelText('Memory text')).not.toBeInTheDocument()
      expect(fake.calls[0]?.body).toEqual({ content: 'Prefers Rust now.' })
    })

    it('cancel puts the old text back and sends nothing', async () => {
      const fake = fakeMemories([memory(1, { content: 'Prefers Go.' })])
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.click(await screen.findByRole('button', { name: 'Edit: Prefers Go.' }))
      await user.type(screen.getByLabelText('Memory text'), ' And more')
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      await user.click(screen.getByRole('button', { name: 'Edit: Prefers Go.' }))

      expect(screen.getByLabelText('Memory text')).toHaveValue('Prefers Go.')
      expect(fake.calls).toHaveLength(0)
    })

    it('stays open with the reason when the text is refused', async () => {
      const fake = fakeMemories([memory(1, { content: 'Prefers Go.' })], (call) =>
        call.method === 'PATCH' && call.path.includes('/memories/')
          ? problem(422, 'VALIDATION_FAILED', 'The request body is invalid.', {
              details: {
                fields: [
                  { loc: ['body', 'content'], msg: 'Not a fact about you.', type: 'value_error' },
                ],
              },
            })
          : undefined,
      )
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.click(await screen.findByRole('button', { name: 'Edit: Prefers Go.' }))
      await user.type(screen.getByLabelText('Memory text'), ' always')
      await user.click(screen.getByRole('button', { name: 'Save' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Not a fact about you.')
      expect(screen.getByLabelText('Memory text')).toBeInTheDocument()
    })
  })

  describe('deleting', () => {
    it('asks first, and cancel keeps the memory', async () => {
      const fake = fakeMemories([memory(1)])
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.click(await screen.findByRole('button', { name: /^Delete: Fact number 1/ }))
      expect(screen.getByText('Delete this memory?')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Cancel' }))

      expect(screen.getByText('Fact number 1.')).toBeInTheDocument()
      expect(fake.calls).toHaveLength(0)
    })

    it('deletes it once confirmed', async () => {
      const fake = fakeMemories([memory(2), memory(1)])
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.click(await screen.findByRole('button', { name: /^Delete: Fact number 1/ }))
      await user.click(screen.getByRole('button', { name: /^Confirm delete/ }))

      await waitFor(() => expect(screen.queryByText('Fact number 1.')).not.toBeInTheDocument())
      expect(screen.getByText('Fact number 2.')).toBeInTheDocument()
      expect(fake.calls.at(-1)?.method).toBe('DELETE')
    })

    it('drops a memory that was already deleted elsewhere', async () => {
      const gone = memory(1)
      const fake = fakeMemories([gone])
      const { user } = renderApp('/settings/memory', fake.handler)
      await user.click(await screen.findByRole('button', { name: /^Delete: Fact number 1/ }))
      fake.rows().length = 0 // deleted in another tab

      await user.click(screen.getByRole('button', { name: /^Confirm delete/ }))

      await waitFor(() => expect(screen.queryByText('Fact number 1.')).not.toBeInTheDocument())
    })
  })

  describe('clear all', () => {
    it('asks for confirmation with the count, then deletes everything', async () => {
      const fake = fakeMemories([memory(2), memory(1)])
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.click(await screen.findByRole('button', { name: 'Clear all' }))
      expect(screen.getByText(/Delete all 2 memories\?/)).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Delete all' }))

      expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument()
      expect(fake.calls.at(-1)).toMatchObject({ method: 'DELETE' })
    })

    it('cancel keeps everything', async () => {
      const fake = fakeMemories([memory(1)])
      const { user } = renderApp('/settings/memory', fake.handler)

      await user.click(await screen.findByRole('button', { name: 'Clear all' }))
      await user.click(screen.getByRole('button', { name: 'Cancel' }))

      expect(screen.getByText('Fact number 1.')).toBeInTheDocument()
      expect(fake.calls).toHaveLength(0)
      expect(await rowOf('Fact number 1.')).toBeInTheDocument()
    })
  })

  it("never renders a memory's text as markup", async () => {
    const fake = fakeMemories([memory(1, { content: '<img src=x onerror=alert(1)> <b>bold</b>' })])
    renderApp('/settings/memory', fake.handler)

    const row = await rowOf('<img src=x onerror=alert(1)> <b>bold</b>')

    expect(row.querySelector('img, b')).toBeNull()
  })
})
