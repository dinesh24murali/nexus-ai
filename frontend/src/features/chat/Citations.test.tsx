import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Citations } from './Citations'

const FILE = {
  index: 1,
  source_type: 'file' as const,
  title: 'q3-report.pdf',
  file_id: '1d2e3f4a-5b6c-4d7e-8f9a-0b1c2d3e4f5a',
  page: 4,
  snippet: 'Monthly churn decreased from 3.8% to 3.1% in Q3.',
}

describe('Citations', () => {
  it('shows one chip per source with its number, file name and page', () => {
    render(
      <Citations
        citations={[FILE, { ...FILE, index: 2, title: 'notes.txt', page: null, snippet: 'x' }]}
      />,
    )

    expect(screen.getByRole('button', { name: '[1] q3-report.pdf p.4' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '[2] notes.txt' })).toBeInTheDocument()
  })

  it('renders nothing for an answer without sources', () => {
    const { container } = render(<Citations citations={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('opens the excerpt in a popover and closes it with Escape or a click elsewhere', async () => {
    const user = userEvent.setup()
    render(
      <div>
        <p>elsewhere</p>
        <Citations citations={[FILE]} />
      </div>,
    )
    const chip = screen.getByRole('button', { name: /q3-report/ })
    expect(chip).toHaveAttribute('aria-expanded', 'false')

    await user.click(chip)
    expect(chip).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('dialog', { name: /Source 1/ })).toHaveTextContent(
      'Monthly churn decreased from 3.8% to 3.1% in Q3.',
    )

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(chip)
    await user.click(screen.getByText('elsewhere'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows one popover at a time', async () => {
    const user = userEvent.setup()
    render(<Citations citations={[FILE, { ...FILE, index: 2, snippet: 'Second excerpt' }]} />)

    await user.click(screen.getByRole('button', { name: /^\[1\]/ }))
    await user.click(screen.getByRole('button', { name: /^\[2\]/ }))

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog')).toHaveTextContent('Second excerpt')
  })

  it('says so when a source has no excerpt', async () => {
    const user = userEvent.setup()
    render(<Citations citations={[{ ...FILE, snippet: undefined }]} />)

    await user.click(screen.getByRole('button', { name: /q3-report/ }))

    expect(screen.getByRole('dialog')).toHaveTextContent('No excerpt available.')
  })

  it('treats a hostile excerpt and title as plain text, never markup', async () => {
    const user = userEvent.setup()
    const evil = '<img src=x onerror=alert(1)><script>alert(2)</script>'
    const { container } = render(
      <Citations citations={[{ ...FILE, title: evil, snippet: evil }]} />,
    )

    await user.click(screen.getByRole('button'))

    expect(container.querySelector('img, script')).toBeNull()
    expect(screen.getByRole('dialog')).toHaveTextContent(evil)
  })

  it('links a web source only when its address is http(s), with a safe rel', async () => {
    const user = userEvent.setup()
    const web = {
      index: 1,
      source_type: 'web' as const,
      title: 'Example',
      url: 'https://example.com/a',
      snippet: 's',
    }
    const { rerender } = render(<Citations citations={[web]} />)
    await user.click(screen.getByRole('button'))
    // The destination is written next to the link, whatever the title says (LLM-T02, phishing).
    const link = screen.getByRole('link', { name: 'Open source (example.com)' })
    expect(link).toHaveAttribute('href', 'https://example.com/a')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).toHaveAttribute('target', '_blank')

    rerender(<Citations citations={[{ ...web, index: 2, url: 'javascript:alert(1)' }]} />)
    await user.click(screen.getByRole('button', { name: /^\[2\]/ }))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('shows the real host, not the title, and a look-alike domain in its punycode form', async () => {
    const user = userEvent.setup()
    const web = {
      index: 1,
      source_type: 'web' as const,
      title: 'paypal.com - sign in',
      url: 'https://xn--pypal-4ve.com/login',
      snippet: 's',
    }
    render(<Citations citations={[web]} />)

    await user.click(screen.getByRole('button'))

    expect(screen.getByRole('link')).toHaveTextContent('Open source (xn--pypal-4ve.com)')
  })

  it('never makes a file source a link, whatever url it carries', async () => {
    const user = userEvent.setup()
    render(<Citations citations={[{ ...FILE, url: 'https://evil.example/' }]} />)

    await user.click(screen.getByRole('button'))

    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
