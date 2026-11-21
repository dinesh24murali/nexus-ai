import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { authBody, json, problem, renderApp, type Call, type Handler } from '../../test-utils'

type Conv = {
  id: string
  title: string
  has_active_run: boolean
  pending_interrupt: null
  created_at: string
  updated_at: string
}

const conv = (n: number, title = `Chat ${n}`): Conv => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  title,
  has_active_run: false,
  pending_interrupt: null,
  created_at: '2026-11-20T10:00:00Z',
  updated_at: '2026-11-20T10:00:00Z',
})

/** A tiny in-memory conversations API: newest first, `pageSize` per page, opaque numeric cursor. */
type Override = (call: Call) => Response | undefined

function fakeApi(initial: Conv[], pageSize = 30, override?: Override) {
  const rows = [...initial]
  let next = initial.length + 1
  const handler: Handler = (call: Call) => {
    if (call.path.endsWith('/auth/refresh')) return json(200, authBody())
    const early = override?.(call)
    if (early) return early
    const { pathname, searchParams } = new URL(call.path, 'http://localhost')
    const id = pathname.split('/conversations/')[1]
    if (pathname.endsWith('/conversations')) {
      if (call.method === 'POST') {
        const created = conv(next++, (call.body as { title?: string })?.title ?? 'New chat')
        rows.unshift(created)
        return json(201, created)
      }
      const start = Number(searchParams.get('cursor') ?? 0)
      const items = rows.slice(start, start + pageSize)
      const more = start + pageSize < rows.length
      return json(200, { items, next_cursor: more ? String(start + pageSize) : null })
    }
    const row = rows.find((r) => r.id === id)
    if (!row) return problem(404, 'NOT_FOUND', 'Conversation not found.')
    if (call.method === 'PATCH') {
      row.title = (call.body as { title: string }).title
      return json(200, row)
    }
    if (call.method === 'DELETE') {
      rows.splice(rows.indexOf(row), 1)
      return new Response(null, { status: 204 })
    }
    return json(200, row)
  }
  return { rows, handler }
}

beforeEach(() => {
  document.documentElement.dataset.theme = 'light'
})

const list = () => screen.findByRole('list', { name: 'Conversations' })
const openMenu = async (user: ReturnType<typeof renderApp>['user'], title: string) =>
  user.click(await screen.findByRole('button', { name: `Actions for ${title}` }))

describe('conversation list', () => {
  it('shows the empty state when there are no conversations', async () => {
    renderApp('/', fakeApi([]).handler)
    expect(await screen.findByText('No conversations yet.')).toBeInTheDocument()
  })

  it('lists the conversations in the order the API returns them', async () => {
    renderApp('/', fakeApi([conv(2), conv(1)]).handler)
    const items = within(await list()).getAllByRole('link')
    expect(items.map((a) => a.textContent)).toEqual(['Chat 2', 'Chat 1'])
    expect(items[0]).toHaveAttribute('href', `/c/${conv(2).id}`)
  })

  it('loads the next page with the cursor when "Load more" is used', async () => {
    const rows = Array.from({ length: 35 }, (_, i) => conv(35 - i))
    const { user, calls } = renderApp('/', fakeApi(rows).handler)
    expect(within(await list()).getAllByRole('link')).toHaveLength(30)

    await user.click(screen.getByRole('button', { name: 'Load more' }))

    await waitFor(() =>
      expect(within(screen.getByRole('list')).getAllByRole('link')).toHaveLength(35),
    )
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
    const paged = calls.filter((c) => c.path.includes('/conversations?'))
    expect(paged.map((c) => new URL(c.path).searchParams.get('cursor'))).toEqual([null, '30'])
  })

  it('sends the access token with every conversation request', async () => {
    const { calls } = renderApp('/', fakeApi([conv(1)]).handler)
    await list()
    const listCall = calls.find((c) => c.path.includes('/conversations'))
    expect(listCall?.headers.get('authorization')).toBe('Bearer tok-1')
  })

  it('shows an error with a retry when the list cannot be loaded', async () => {
    let fail = true
    const { user } = renderApp(
      '/',
      fakeApi([conv(1)], 30, (call) =>
        fail && call.method === 'GET' && call.path.includes('/conversations')
          ? problem(500, 'INTERNAL_ERROR')
          : undefined,
      ).handler,
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    fail = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: 'Chat 1' })).toBeInTheDocument()
  })
})

describe('new chat', () => {
  it('creates a conversation, opens it and adds it to the sidebar', async () => {
    const { user, calls } = renderApp('/', fakeApi([conv(1)]).handler)
    await list()

    await user.click(screen.getByRole('button', { name: 'New chat' }))

    expect(await screen.findByRole('heading', { name: 'New chat' })).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/conversations'))).toBe(true)
    const links = within(await list()).getAllByRole('link')
    expect(links[0]).toHaveTextContent('New chat')
    expect(links[0]).toHaveAttribute('aria-current', 'page')
  })

  it('reports a failure to create', async () => {
    const { user } = renderApp(
      '/',
      fakeApi([], 30, (c) => (c.method === 'POST' ? problem(500, 'INTERNAL_ERROR') : undefined))
        .handler,
    )
    await user.click(await screen.findByRole('button', { name: 'New chat' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
  })
})

describe('rename', () => {
  it('renames from the actions menu, trimming the title', async () => {
    const api = fakeApi([conv(1, 'Old name')])
    const { user, calls } = renderApp('/', api.handler)
    await openMenu(user, 'Old name')
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }))

    const input = screen.getByRole('textbox', { name: 'Conversation title' })
    await user.clear(input)
    await user.type(input, '  Q3 churn analysis  {Enter}')

    expect(await screen.findByRole('link', { name: 'Q3 churn analysis' })).toBeInTheDocument()
    const patch = calls.find((c) => c.method === 'PATCH')
    expect(patch?.body).toEqual({ title: 'Q3 churn analysis' })
    expect(api.rows[0].title).toBe('Q3 churn analysis')
  })

  it('Escape cancels without calling the API', async () => {
    const { user, calls } = renderApp('/', fakeApi([conv(1, 'Keep me')]).handler)
    await openMenu(user, 'Keep me')
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }))
    await user.type(screen.getByRole('textbox', { name: 'Conversation title' }), 'xyz{Escape}')

    expect(await screen.findByRole('link', { name: 'Keep me' })).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false)
  })

  it('does not allow an empty title', async () => {
    const { user, calls } = renderApp('/', fakeApi([conv(1, 'Keep me')]).handler)
    await openMenu(user, 'Keep me')
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }))
    await user.clear(screen.getByRole('textbox', { name: 'Conversation title' }))
    await user.keyboard('{Enter}')

    expect(screen.getByRole('button', { name: 'Save title' })).toBeDisabled()
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false)
  })

  it('keeps the form open and shows the error when the server refuses', async () => {
    const { user } = renderApp(
      '/',
      fakeApi([conv(1, 'Old')], 30, (c) =>
        c.method === 'PATCH' ? problem(422, 'VALIDATION_FAILED') : undefined,
      ).handler,
    )
    await openMenu(user, 'Old')
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }))
    await user.type(screen.getByRole('textbox', { name: 'Conversation title' }), 'x{Enter}')

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Conversation title' })).toBeInTheDocument()
  })
})

describe('delete', () => {
  it('asks for confirmation, then deletes', async () => {
    const api = fakeApi([conv(2), conv(1)])
    const { user, calls } = renderApp('/', api.handler)
    await openMenu(user, 'Chat 2')
    await user.click(screen.getByRole('menuitem', { name: 'Delete' }))

    const confirm = screen.getByRole('group', { name: 'Confirm delete' })
    expect(confirm).toHaveTextContent('Chat 2')
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false) // nothing happens until confirmed
    await user.click(within(confirm).getByRole('button', { name: 'Delete' }))

    await waitFor(() =>
      expect(screen.queryByRole('link', { name: 'Chat 2' })).not.toBeInTheDocument(),
    )
    expect(screen.getByRole('link', { name: 'Chat 1' })).toBeInTheDocument()
    expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual([
      expect.stringMatching(new RegExp(`/conversations/${conv(2).id}$`)),
    ])
  })

  it('Cancel leaves the conversation alone', async () => {
    const { user, calls } = renderApp('/', fakeApi([conv(1)]).handler)
    await openMenu(user, 'Chat 1')
    await user.click(screen.getByRole('menuitem', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByRole('link', { name: 'Chat 1' })).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
  })

  it('deleting the open conversation returns to the home page', async () => {
    const { user } = renderApp(`/c/${conv(1).id}`, fakeApi([conv(1)]).handler)
    expect(await screen.findByRole('heading', { name: 'Chat 1', level: 1 })).toBeInTheDocument()

    await user.click(await screen.findByRole('button', { name: 'Actions for Chat 1' }))
    await user.click(screen.getByRole('menuitem', { name: 'Delete' }))
    await user.click(
      within(screen.getByRole('group', { name: 'Confirm delete' })).getByRole('button', {
        name: 'Delete',
      }),
    )

    expect(await screen.findByRole('heading', { name: /hello, marco/i })).toBeInTheDocument()
  })

  it('shows the error and keeps the conversation when the delete fails', async () => {
    const { user } = renderApp(
      '/',
      fakeApi([conv(1)], 30, (c) =>
        c.method === 'DELETE' ? problem(500, 'INTERNAL_ERROR') : undefined,
      ).handler,
    )
    await openMenu(user, 'Chat 1')
    await user.click(screen.getByRole('menuitem', { name: 'Delete' }))
    await user.click(
      within(screen.getByRole('group', { name: 'Confirm delete' })).getByRole('button', {
        name: 'Delete',
      }),
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('group', { name: 'Confirm delete' })).toBeInTheDocument()
  })
})

describe('conversation page', () => {
  it('says so when the conversation does not exist or is not yours', async () => {
    renderApp('/c/00000000-0000-4000-8000-0000000000ff', fakeApi([conv(1)]).handler)
    expect(
      await screen.findByRole('heading', { name: 'Conversation not found' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start a new chat' })).toHaveAttribute('href', '/')
  })
})
