import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ToolCalls, ToolCard } from './ToolCard'
import { labelOf, toView, type ToolCallView, type ToolStatus } from './toolCalls'

const call = (over: Partial<ToolCallView> = {}): ToolCallView => ({
  id: 'call_1',
  name: 'search_my_files',
  args: { query: 'Q3 churn' },
  status: 'completed',
  ...over,
})

const STATUSES: ToolStatus[] = ['running', 'completed', 'failed', 'rejected', 'timed_out']

describe('ToolCard status', () => {
  it.each([
    ['running', 'Searching your files'],
    ['completed', 'Searched your files'],
    ['failed', 'File search failed'],
    ['rejected', 'File search declined'],
    ['timed_out', 'File search timed out'],
  ] as const)('%s reads "%s"', (status, words) => {
    render(<ToolCard call={call({ status })} />)

    const card = screen.getByRole('group', { name: words })
    expect(card).toHaveAttribute('data-status', status)
    expect(card).toHaveTextContent('Q3 churn')
  })

  it('only a running call is a live status (and it has the spinner)', () => {
    const { container, rerender } = render(<ToolCard call={call({ status: 'running' })} />)
    expect(screen.getByRole('status')).toHaveTextContent('Searching your files')
    expect(container.querySelector('.animate-spin')).not.toBeNull()

    rerender(<ToolCard call={call({ status: 'completed' })} />)
    expect(screen.queryByRole('status')).toBeNull()
    expect(container.querySelector('.animate-spin')).toBeNull()
  })

  it('shows the summary the tool reported, and marks a bad outcome', () => {
    const { rerender } = render(<ToolCard call={call({ summary: '4 passages found' })} />)
    expect(screen.getByText('4 passages found')).toHaveClass('text-fg-muted')

    rerender(<ToolCard call={call({ status: 'timed_out', summary: 'The search timed out' })} />)
    expect(screen.getByText('The search timed out')).toHaveClass('text-error-fg')
  })

  it('has a distinct label for every status of every tool', () => {
    for (const name of ['search_my_files', 'web_search', 'calculator']) {
      const labels = STATUSES.map((status) => labelOf({ name, status }))
      expect(new Set(labels).size).toBe(STATUSES.length)
    }
  })
})

describe('ToolCard tools', () => {
  it('web search: the query is the headline', () => {
    render(<ToolCard call={call({ name: 'web_search', args: { query: 'Go release notes' } })} />)

    expect(screen.getByRole('group', { name: 'Searched the web' })).toHaveTextContent(
      'Go release notes',
    )
  })

  it('calculator: the expression is the headline', () => {
    render(
      <ToolCard
        call={call({
          name: 'calculator',
          args: { expression: '(17.5 / 100) * 2348901' },
          summary: '411057.675',
        })}
      />,
    )

    const card = screen.getByRole('group', { name: 'Calculated' })
    expect(card).toHaveTextContent('(17.5 / 100) * 2348901')
    expect(card).toHaveTextContent('411057.675')
  })

  it('file search: shows how many files it was limited to, never their ids', () => {
    render(
      <ToolCard
        call={call({
          args: { query: 'churn', file_ids: ['1d2e3f4a-5b6c-4d7e-8f9a-0b1c2d3e4f5a'] },
        })}
      />,
    )

    const card = screen.getByRole('group', { name: 'Searched your files' })
    expect(card).toHaveTextContent('in: 1 selected file')
    expect(card).not.toHaveTextContent('1d2e3f4a')
  })

  it('a tool this build does not know is shown by its name, with all its arguments', () => {
    render(<ToolCard call={call({ name: 'future_tool', args: { city: 'Pune', days: 3 } })} />)

    const card = screen.getByRole('group', { name: 'future_tool: done' })
    expect(card).toHaveTextContent('city: Pune')
    expect(card).toHaveTextContent('days: 3')
  })

  it('a call with no usable headline still renders', () => {
    render(<ToolCard call={call({ args: {} })} />)

    expect(screen.getByRole('group', { name: 'Searched your files' })).toBeInTheDocument()
  })
})

describe('ToolCard safety', () => {
  it('shows model-controlled text as text, never as markup', () => {
    const { container } = render(
      <ToolCard
        call={call({
          args: { query: '<img src=x onerror=alert(1)> <b>bold</b>' },
          summary: '<script>alert(2)</script>',
        })}
      />,
    )

    expect(container.querySelector('img, b, script')).toBeNull()
    expect(container).toHaveTextContent('<b>bold</b>')
    expect(container).toHaveTextContent('<script>alert(2)</script>')
  })

  it('does not turn a link-looking argument into a link', () => {
    const { container } = render(
      <ToolCard call={call({ args: { query: 'javascript:alert(1)' } })} />,
    )

    expect(container.querySelector('a')).toBeNull()
  })
})

describe('ToolCalls', () => {
  it('lists the calls in order, one card each', () => {
    render(
      <ToolCalls
        calls={[
          call({ id: 'a', name: 'web_search', args: { query: 'one' } }),
          call({ id: 'b', name: 'calculator', args: { expression: '1+1' } }),
        ]}
      />,
    )

    const list = screen.getByRole('list', { name: 'Tool calls' })
    expect(list.querySelectorAll('[role="group"]')).toHaveLength(2)
    expect(list.textContent?.indexOf('one')).toBeLessThan(list.textContent?.indexOf('1+1') ?? -1)
  })

  it('renders nothing for no calls', () => {
    const { container } = render(<ToolCalls calls={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('converts a stored call without losing anything', () => {
    const stored = {
      id: 'c',
      name: 'web_search' as const,
      args: { query: 'x' },
      status: 'rejected' as const,
      summary: 'Declined by you',
    }

    expect(toView(stored)).toEqual(stored)
  })
})
