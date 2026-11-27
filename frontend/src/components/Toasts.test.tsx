import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from './Toasts'
import { TOAST_MS, useToasts, type ToastInput } from './toast-context'

function Trigger({ toast }: { toast: ToastInput }) {
  const { show } = useToasts()
  return <button onClick={() => show(toast)}>show</button>
}

const setup = (toast: ToastInput) =>
  render(
    <MemoryRouter>
      <ToastProvider>
        <Trigger toast={toast} />
      </ToastProvider>
    </MemoryRouter>,
  )

afterEach(() => vi.useRealTimers())

describe('toasts', () => {
  it('announces a message politely and can link somewhere', async () => {
    const user = userEvent.setup()
    setup({ message: 'Nexus remembered 1 thing.', action: { label: 'Manage', to: '/x' } })

    await user.click(screen.getByRole('button', { name: 'show' }))

    const region = screen.getByRole('region', { name: 'Notifications' })
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region).toHaveTextContent('Nexus remembered 1 thing.')
    expect(screen.getByRole('link', { name: 'Manage' })).toHaveAttribute('href', '/x')
  })

  it('goes away when dismissed', async () => {
    const user = userEvent.setup()
    setup({ message: 'Hello' })
    await user.click(screen.getByRole('button', { name: 'show' }))

    await user.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(screen.queryByText('Hello')).not.toBeInTheDocument()
  })

  it('goes away by itself after a few seconds', () => {
    vi.useFakeTimers()
    setup({ message: 'Hello' })
    act(() => screen.getByRole('button', { name: 'show' }).click())
    expect(screen.getByText('Hello')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(TOAST_MS + 10)
    })

    expect(screen.queryByText('Hello')).not.toBeInTheDocument()
  })

  it('is a harmless no-op without a provider', async () => {
    const user = userEvent.setup()
    render(<Trigger toast={{ message: 'Hello' }} />)

    await user.click(screen.getByRole('button', { name: 'show' }))

    expect(screen.queryByText('Hello')).not.toBeInTheDocument()
  })
})
