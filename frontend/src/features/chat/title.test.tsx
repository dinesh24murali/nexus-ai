import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { answerStream, json, problem, renderApp, signedIn } from '../../test-utils'

// Shrink the waits so the test does not sit through 1.5 s + 4 s.
vi.mock('./timing', () => ({ TITLE_REFRESH_DELAYS_MS: [20, 60, 120], TOKEN_BATCH_MS: 1 }))

const CID = '00000000-0000-4000-8000-000000000001'
const conv = (title: string) => ({
  id: CID,
  title,
  has_active_run: false,
  pending_interrupt: null,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
})

describe('title refresh', () => {
  it('picks up the generated title once the worker has set it, then stops asking', async () => {
    let title = 'New chat'
    let reads = 0
    const { user, calls } = renderApp(
      `/c/${CID}`,
      signedIn((call) => {
        const path = new URL(call.path, 'http://localhost').pathname.replace('/api/v1', '')
        if (path === '/me/usage') {
          return json(200, {
            day: '2026-11-21',
            spend_usd: 0,
            budget_usd: 0.5,
            tokens_used: 0,
            messages: 0,
            global_cap_reached: false,
            resets_at: '2026-11-22T00:00:00Z',
          })
        }
        if (path === `/conversations/${CID}` && call.method === 'GET') {
          reads++
          return json(200, conv(title))
        }
        if (path === `/conversations/${CID}/messages` && call.method === 'GET') {
          return json(200, { items: [], next_cursor: null })
        }
        if (path === `/conversations/${CID}/messages` && call.method === 'POST') {
          // the worker names the conversation shortly after the first answer
          setTimeout(() => (title = 'Trip to Lisbon'), 70)
          return answerStream('Sure')
        }
        return problem(500, 'INTERNAL_ERROR')
      }),
    )
    await screen.findByRole('heading', { level: 1, name: 'New chat' })
    expect(reads).toBe(1)

    await user.type(await screen.findByRole('textbox', { name: 'Message' }), 'plan a trip{Enter}')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Trip to Lisbon' }),
    ).toBeInTheDocument()
    const readsAfterTitle = reads
    await new Promise((r) => setTimeout(r, 200))
    expect(reads).toBe(readsAfterTitle) // no more polling once the title is no longer the default
    void calls
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument())
  })
})
