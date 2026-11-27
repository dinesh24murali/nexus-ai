import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { components } from '../../api/schema'
import { ApprovalCard } from './ApprovalCard'

type PendingInterrupt = components['schemas']['PendingInterrupt']

const NOW = new Date('2026-11-21T10:00:00Z').getTime()
const iso = (ms: number) => new Date(ms).toISOString()

const approval = (overrides: Partial<PendingInterrupt> = {}): PendingInterrupt => ({
  interrupt_id: 'int_9a8b7c',
  tool: 'web_search',
  args: { query: 'Go 1.24 release notes' },
  created_at: iso(NOW),
  expires_at: iso(NOW + 24 * 60 * 60 * 1000),
  ...overrides,
})

function setup(overrides: Partial<PendingInterrupt> = {}, props: { busy?: boolean } = {}) {
  const onDecide = vi.fn()
  render(
    <ApprovalCard interrupt={approval(overrides)} onDecide={onDecide} now={() => NOW} {...props} />,
  )
  return { onDecide, user: userEvent.setup() }
}

afterEach(() => vi.useRealTimers())

describe('ApprovalCard', () => {
  it('asks before searching and shows the exact query', () => {
    setup()

    expect(screen.getByRole('group', { name: 'Search the web?' })).toBeInTheDocument()
    expect(screen.getByText('Go 1.24 release notes')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled()
  })

  it('shows the query as plain text, whatever it contains', () => {
    setup({ args: { query: '<img src=x onerror=alert(1)> & "quotes"' } })

    expect(screen.getByText('<img src=x onerror=alert(1)> & "quotes"')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('approves with one click', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'approve' })
  })

  it('rejects with one click', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Reject' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'reject' })
  })

  it('lets the query be edited before it is searched', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const box = screen.getByRole('textbox', { name: 'Search query' })
    expect(box).toHaveValue('Go 1.24 release notes')
    await user.clear(box)
    await user.type(box, 'Go 1.24 generics')
    await user.click(screen.getByRole('button', { name: 'Search with this query' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({
      decision: 'edit',
      query: 'Go 1.24 generics',
    })
  })

  it('submits an edit with Enter', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.type(screen.getByRole('textbox', { name: 'Search query' }), ' generics{Enter}')

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({
      decision: 'edit',
      query: 'Go 1.24 release notes generics',
    })
  })

  it('sends a plain approval when the query was not changed', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Search with this query' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'approve' })
  })

  it('trims the edited query', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const box = screen.getByRole('textbox', { name: 'Search query' })
    await user.clear(box)
    await user.type(box, '   Go modules   ')
    await user.click(screen.getByRole('button', { name: 'Search with this query' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'edit', query: 'Go modules' })
  })

  it('does not search for an empty query', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.clear(screen.getByRole('textbox', { name: 'Search query' }))
    await user.type(screen.getByRole('textbox', { name: 'Search query' }), '   ')

    expect(screen.getByRole('button', { name: 'Search with this query' })).toBeDisabled()
    await user.keyboard('{Enter}')
    expect(onDecide).not.toHaveBeenCalled()
  })

  it('limits the query to 400 characters, like the API', async () => {
    setup()

    await userEvent.setup().click(screen.getByRole('button', { name: 'Edit' }))

    expect(screen.getByRole('textbox', { name: 'Search query' })).toHaveAttribute(
      'maxlength',
      '400',
    )
  })

  it('goes back to the proposed query when editing is cancelled', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.type(screen.getByRole('textbox', { name: 'Search query' }), ' changed')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByText('Go 1.24 release notes')).toBeInTheDocument()
    expect(onDecide).not.toHaveBeenCalled()
  })

  it('cannot be answered twice while a decision is on its way', () => {
    setup({}, { busy: true })

    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled()
  })

  it('shows an expired approval as expired, without buttons', () => {
    setup({ expires_at: iso(NOW - 1000) })

    expect(screen.getByText(/expired/i)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('Go 1.24 release notes')).toBeInTheDocument()
  })

  it('turns into an expired card when its time runs out', () => {
    vi.useFakeTimers({ now: NOW })
    const onDecide = vi.fn()
    render(
      <ApprovalCard
        interrupt={approval({ expires_at: iso(NOW + 5000) })}
        onDecide={onDecide}
        now={Date.now}
      />,
    )
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(5001)
    })

    expect(screen.getByText(/expired/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('does not know how to ask about other tools yet, and says so', async () => {
    // A tool this build has never heard of (a newer server): it can only be rejected.
    const { onDecide, user } = setup({
      tool: 'calendar_event' as PendingInterrupt['tool'],
      args: { title: 'x' },
    })

    expect(screen.getByText(/can't be shown here yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reject' }))
    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'reject' })
  })
})

describe('ApprovalCard: forgetting a memory', () => {
  const memory = (id: string, content: string) => ({ memory_id: id, content })
  const forget = (candidates: unknown, overrides: Partial<PendingInterrupt> = {}) =>
    approval({ tool: 'forget_memory', args: { candidates }, ...overrides })

  it('asks before forgetting and shows the exact text of the one memory it found', () => {
    setup(forget([memory('m1', 'User works at Acme.')]))

    expect(screen.getByRole('group', { name: 'Forget a memory?' })).toBeInTheDocument()
    expect(screen.getByText('User works at Acme.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Forget it' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Keep it' })).toBeEnabled()
    expect(screen.queryByRole('radio')).toBeNull()
  })

  it('forgets the single memory with one click, naming it', async () => {
    const { onDecide, user } = setup(forget([memory('m1', 'User works at Acme.')]))

    await user.click(screen.getByRole('button', { name: 'Forget it' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'approve', memoryId: 'm1' })
  })

  it('keeps everything on request', async () => {
    const { onDecide, user } = setup(forget([memory('m1', 'User works at Acme.')]))

    await user.click(screen.getByRole('button', { name: 'Keep it' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'reject' })
  })

  it('lets the user pick one of several, and offers nothing until they do', async () => {
    const { onDecide, user } = setup(
      forget([memory('m1', 'User works at Acme.'), memory('m2', 'User works remotely.')]),
    )

    expect(
      screen.getByRole('group', { name: 'Which of these should Nexus forget?' }),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(2)
    expect(screen.getAllByRole('radio').every((r) => !(r as HTMLInputElement).checked)).toBe(true)
    expect(screen.getByRole('button', { name: 'Forget the selected memory' })).toBeDisabled()

    await user.click(screen.getByRole('radio', { name: 'User works remotely.' }))
    await user.click(screen.getByRole('button', { name: 'Forget the selected memory' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'approve', memoryId: 'm2' })
  })

  it('can keep them all when there are several', async () => {
    const { onDecide, user } = setup(
      forget([memory('m1', 'User works at Acme.'), memory('m2', 'User works remotely.')]),
    )

    await user.click(screen.getByRole('button', { name: 'Keep them all' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'reject' })
  })

  it('shows memory text as plain text, whatever it contains', () => {
    setup(forget([memory('m1', '<img src=x onerror=alert(1)> & "quotes"')]))

    expect(screen.getByText('<img src=x onerror=alert(1)> & "quotes"')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('waits while a decision is on its way', () => {
    setup(forget([memory('m1', 'User works at Acme.')]), { busy: true })

    expect(screen.getByRole('button', { name: 'Forget it' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Keep it' })).toBeDisabled()
  })

  it('shows "expired" and no buttons once the approval has expired', () => {
    setup(forget([memory('m1', 'User works at Acme.')], { expires_at: iso(NOW - 1000) }))

    expect(screen.getByRole('status')).toHaveTextContent('This approval expired')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('does not break on a card it cannot read, and only offers to keep everything', async () => {
    const { onDecide, user } = setup(forget([{ memory_id: 7 }, 'x', null]))

    expect(screen.queryByRole('button', { name: /Forget/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'reject' })
  })
})
