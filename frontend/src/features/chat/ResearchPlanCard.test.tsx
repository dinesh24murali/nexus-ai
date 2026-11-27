import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PendingInterrupt } from './approvals'
import { ApprovalCard } from './ApprovalCard'

const NOW = new Date('2026-11-21T10:00:00Z').getTime()
const iso = (ms: number) => new Date(ms).toISOString()
const QUERIES = [
  'solid-state batteries',
  'solid-state batteries latest developments',
  'solid-state batteries cost',
  'solid-state batteries criticism',
]

const plan = (over: Partial<PendingInterrupt> = {}): PendingInterrupt => ({
  interrupt_id: 'int_plan1',
  tool: 'research_plan',
  args: { topic: 'solid-state batteries', queries: QUERIES },
  created_at: iso(NOW),
  expires_at: iso(NOW + 24 * 60 * 60 * 1000),
  ...over,
})

function setup(over: Partial<PendingInterrupt> = {}, props: { busy?: boolean } = {}) {
  const onDecide = vi.fn()
  render(<ApprovalCard interrupt={plan(over)} onDecide={onDecide} now={() => NOW} {...props} />)
  return { onDecide, user: userEvent.setup() }
}

const card = () => screen.getByRole('group', { name: 'Research plan' })

afterEach(() => vi.useRealTimers())

describe('ResearchPlanCard', () => {
  it('shows the topic and every query before anything is searched', () => {
    setup()

    expect(within(card()).getByText('solid-state batteries', { selector: 'span' })).toBeVisible()
    const items = within(card()).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual(QUERIES)
    expect(screen.getByText(/Nothing is searched until you approve/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Approve and search' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()
    expect(screen.getByRole('button', { name: "Don't search" })).toBeEnabled()
  })

  it('shows everything as text, whatever it contains', () => {
    setup({
      args: {
        topic: '<script>alert(1)</script>',
        queries: ['<img src=x onerror=alert(1)> & "quotes"'],
      },
    })

    expect(screen.getByText('<img src=x onerror=alert(1)> & "quotes"')).toBeVisible()
    expect(screen.getByText('<script>alert(1)</script>')).toBeVisible()
    expect(document.querySelector('img, script')).toBeNull()
  })

  it('approves the plan as shown with one click', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: 'Approve and search' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'approve' })
  })

  it('rejects the plan with one click', async () => {
    const { onDecide, user } = setup()

    await user.click(screen.getByRole('button', { name: "Don't search" }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'reject' })
  })

  it('lets the queries be edited: change one, remove one, add one', async () => {
    const { onDecide, user } = setup()
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    const first = screen.getByRole('textbox', { name: 'Search query 1' })
    await user.clear(first)
    await user.type(first, '  my own query  ')
    await user.click(screen.getByRole('button', { name: 'Remove query 4' }))
    await user.click(screen.getByRole('button', { name: 'Add a query' }))
    await user.type(screen.getByRole('textbox', { name: 'Search query 4' }), 'a new one')
    await user.click(screen.getByRole('button', { name: 'Search with these queries' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({
      decision: 'edit',
      queries: ['my own query', QUERIES[1], QUERIES[2], 'a new one'],
    })
  })

  it('sends a plain approval when the queries were not changed', async () => {
    const { onDecide, user } = setup()
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    await user.click(screen.getByRole('button', { name: 'Search with these queries' }))

    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'approve' })
  })

  it('drops blank queries and will not search with none', async () => {
    const { onDecide, user } = setup({ args: { topic: 't', queries: ['one', 'two'] } })
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    await user.clear(screen.getByRole('textbox', { name: 'Search query 1' }))
    await user.click(screen.getByRole('button', { name: 'Search with these queries' }))
    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'edit', queries: ['two'] })

    onDecide.mockClear()
    await user.clear(screen.getByRole('textbox', { name: 'Search query 2' }))
    expect(screen.getByRole('button', { name: 'Search with these queries' })).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Search query 2' }), '{Enter}')
    expect(onDecide).not.toHaveBeenCalled()
  })

  it('keeps at least one query and at most six, and limits a query to 400 characters', async () => {
    const { user } = setup({ args: { topic: 't', queries: ['only'] } })
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('button', { name: 'Remove query 1' })).toBeDisabled()

    for (let i = 0; i < 5; i += 1) {
      await user.click(screen.getByRole('button', { name: 'Add a query' }))
    }
    expect(screen.getAllByRole('textbox')).toHaveLength(6)
    expect(screen.getByRole('button', { name: 'Add a query' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Search query 1' })).toHaveAttribute(
      'maxlength',
      '400',
    )
  })

  it('goes back to the proposed queries when editing is cancelled', async () => {
    const { onDecide, user } = setup()
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.type(screen.getByRole('textbox', { name: 'Search query 1' }), ' changed')

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(
      within(card())
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(QUERIES)
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('textbox', { name: 'Search query 1' })).toHaveValue(QUERIES[0])
    expect(onDecide).not.toHaveBeenCalled()
  })

  it('cannot be answered twice while a decision is on its way', () => {
    setup({}, { busy: true })

    for (const name of ['Approve and search', 'Edit', "Don't search"]) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
  })

  it('shows an expired plan as expired, without buttons, and flips when its time runs out', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    const onDecide = vi.fn()
    render(
      <ApprovalCard
        interrupt={plan({ expires_at: iso(NOW + 1000) })}
        onDecide={onDecide}
        now={() => Date.now()}
      />,
    )
    expect(screen.getByRole('button', { name: 'Approve and search' })).toBeEnabled()

    act(() => {
      vi.advanceTimersByTime(1500)
    })

    expect(screen.getByRole('status')).toHaveTextContent('This approval expired')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('does not break on a plan it cannot read, and only offers to reject it', async () => {
    const { onDecide, user } = setup({ args: { topic: 5, queries: 'nope' } })

    expect(screen.getByText(/This plan can.t be shown/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Approve and search' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reject' }))
    expect(onDecide).toHaveBeenCalledExactlyOnceWith({ decision: 'reject' })
  })
})
