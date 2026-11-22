import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  done,
  IDS,
  json,
  liveStream,
  problem,
  renderApp,
  runStarted,
  signedIn,
  sseResponse,
  token,
  usageEvent,
  type Call,
  type Handler,
} from '../../test-utils'
import type { DocumentObject } from './api'

// A pending document is read again after 20 ms instead of 2 s.
vi.mock('./timing', () => ({ POLL_MS: 20 }))

const CID = '00000000-0000-4000-8000-000000000001'

const doc = (n: number, over: Partial<DocumentObject> = {}): DocumentObject => ({
  id: `00000000-0000-4000-a000-${String(n).padStart(12, '0')}`,
  title: `Report ${n}`,
  kind: 'thread_summary',
  status: 'ready',
  conversation_id: CID,
  message_id: null,
  page_count: 3,
  size_bytes: 84211,
  error: null,
  created_at: '2026-11-20T10:00:00Z',
  ...over,
})

type Fake = {
  rows: DocumentObject[]
  calls: Call[]
  /** What GET /documents/{id} answers instead of the row (a scripted sequence). */
  one?: (id: string) => Response | undefined
  /** The assistant message the conversation page shows, with these documents attached. */
  attached?: DocumentObject[]
  /** What the download answers. */
  download?: () => Response
}

/** A small in-memory documents API (and the one conversation the card tests open). */
function fakeDocuments(seed: Partial<Fake> = {}) {
  const server: Fake = { rows: [], calls: [], ...seed }
  const handler: Handler = signedIn((call) => {
    server.calls.push(call)
    const path = new URL(call.path, 'http://localhost').pathname.replace('/api/v1', '')
    if (path === '/me/usage') return problem(500, 'INTERNAL_ERROR')
    if (path === `/conversations/${CID}` && call.method === 'GET') {
      return json(200, {
        id: CID,
        title: 'Planning',
        has_active_run: false,
        pending_interrupt: null,
        created_at: '2026-11-21T10:00:00Z',
        updated_at: '2026-11-21T10:00:00Z',
      })
    }
    if (path === `/conversations/${CID}/messages` && call.method === 'GET') {
      const items = server.attached
        ? [
            {
              id: IDS.assistant,
              conversation_id: CID,
              role: 'assistant',
              content: 'Your PDF is on its way.',
              status: 'complete',
              finish_reason: 'stop',
              intent: 'generate_document',
              route_source: 'command',
              documents: server.attached,
              created_at: '2026-11-21T10:00:00Z',
            },
          ]
        : []
      return json(200, { items, next_cursor: null })
    }
    if (path === '/documents' && call.method === 'GET') {
      return json(200, { items: server.rows, next_cursor: null })
    }
    const download = /^\/documents\/([^/]+)\/download$/.exec(path)
    if (download) {
      return server.download?.() ?? new Response('%PDF-1.7 fake', { status: 200 })
    }
    const one = /^\/documents\/([^/]+)$/.exec(path)
    if (one) {
      const id = one[1]!
      const early = server.one?.(id)
      if (early) return early
      const row = server.rows.find((r) => r.id === id)
      if (call.method === 'DELETE') {
        if (!row) return problem(404, 'NOT_FOUND', 'Document not found.')
        server.rows.splice(server.rows.indexOf(row), 1)
        return new Response(null, { status: 204 })
      }
      return row ? json(200, row) : problem(404, 'NOT_FOUND', 'Document not found.')
    }
    return problem(500, 'INTERNAL_ERROR', `unscripted ${call.method} ${path}`)
  })
  return { server, handler }
}

/** Catch the file the page saves: jsdom has no object URLs and does not download. */
let saved: { filename: string; blob: Blob }[] = []
let blobs = new Map<string, Blob>()

beforeEach(() => {
  document.documentElement.dataset.theme = 'light'
  saved = []
  blobs = new Map()
  let n = 0
  URL.createObjectURL = vi.fn((blob: Blob | MediaSource) => {
    const url = `blob:test/${++n}`
    blobs.set(url, blob as Blob)
    return url
  })
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push({ filename: this.download, blob: blobs.get(this.href)! })
  })
})

afterEach(() => vi.restoreAllMocks())

// jsdom's Blob is not Node's: read it the way a browser page would.
const pdfText = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })

describe('the document card under an answer', () => {
  it('follows the render: Preparing, then Ready with pages, size and Download', async () => {
    const { server, handler } = fakeDocuments({ attached: [doc(1, { status: 'pending' })] })
    server.rows = [doc(1, { status: 'pending' })]
    renderApp(`/c/${CID}`, handler)

    const card = await screen.findByRole('region', { name: 'Document: Report 1' })
    expect(within(card).getByText('Preparing the PDF…')).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /download/i })).not.toBeInTheDocument()

    server.rows[0] = doc(1) // the worker finished
    expect(await within(card).findByText('3 pages · 82 KB')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Download Report 1' })).toBeEnabled()
    expect(within(card).getByText('Conversation summary')).toBeInTheDocument()
    const reads = server.calls.filter((c) => c.path.endsWith(`/documents/${doc(1).id}`)).length
    await new Promise((r) => setTimeout(r, 120))
    // It stopped asking once the render was over.
    expect(server.calls.filter((c) => c.path.endsWith(`/documents/${doc(1).id}`))).toHaveLength(
      reads,
    )
  })

  it('downloads with the bearer token and saves the bytes under a safe file name', async () => {
    const titled = doc(1, { title: 'Q3 churn: "the plan"/..\\x' })
    const { server, handler } = fakeDocuments({ attached: [titled] })
    server.rows = [titled]
    const { user } = renderApp(`/c/${CID}`, handler)

    await user.click(await screen.findByRole('button', { name: /^Download Q3 churn/ }))

    await waitFor(() => expect(saved).toHaveLength(1))
    expect(saved[0]!.filename).toBe('Q3 churn the plan x.pdf')
    expect(await pdfText(saved[0]!.blob)).toBe('%PDF-1.7 fake')
    expect(saved[0]!.blob.type).toBe('application/pdf')
    const call = server.calls.find((c) => c.path.endsWith('/download'))!
    expect(call.path).toBe(`${location.origin}/api/v1/documents/${doc(1).id}/download`)
    expect(call.headers.get('authorization')).toBe('Bearer tok-1')
  })

  it('says why a download did not happen, and can be tried again', async () => {
    let calls = 0
    const { server, handler } = fakeDocuments({
      attached: [doc(1)],
      download: () => {
        calls += 1
        return calls === 1
          ? problem(409, 'DOCUMENT_NOT_READY', 'The PDF is still being prepared.')
          : new Response('%PDF-1.7 ok', { status: 200 })
      },
    })
    server.rows = [doc(1)]
    const { user } = renderApp(`/c/${CID}`, handler)

    await user.click(await screen.findByRole('button', { name: 'Download Report 1' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The PDF is not ready yet.')
    expect(saved).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Download Report 1' }))
    await waitFor(() => expect(saved).toHaveLength(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the reason a render failed and offers no download', async () => {
    const failed = doc(1, {
      status: 'failed',
      error: 'The document was too long to render.',
      page_count: null,
      size_bytes: null,
    })
    const { server, handler } = fakeDocuments({ attached: [failed] })
    server.rows = [failed]
    renderApp(`/c/${CID}`, handler)

    const card = await screen.findByRole('region', { name: 'Document: Report 1' })
    expect(await within(card).findByText('The document was too long to render.')).toBeVisible()
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
  })

  it('says so when the document was deleted meanwhile', async () => {
    const { handler } = fakeDocuments({ attached: [doc(1)] }) // the list is empty: a 404
    renderApp(`/c/${CID}`, handler)

    const card = await screen.findByRole('region', { name: 'Document: Report 1' })
    expect(await within(card).findByText('This document was deleted.')).toBeVisible()
    expect(within(card).queryByRole('button', { name: /download/i })).not.toBeInTheDocument()
  })

  it('shows a title as text, never as markup', async () => {
    const title = '<img src=x onerror=alert(1)> <b>bold</b>'
    const { server, handler } = fakeDocuments({ attached: [doc(1, { title })] })
    server.rows = [doc(1, { title })]
    renderApp(`/c/${CID}`, handler)

    const card = await screen.findByRole('region', { name: `Document: ${title}` })
    expect(within(card).getByText(title)).toBeInTheDocument()
    expect(card.querySelector('img, b')).toBeNull()
  })

  it('appears live from document_created and is not shown twice once the answer is stored', async () => {
    const { server, handler } = fakeDocuments()
    server.rows = [doc(1)]
    const { user } = renderApp(`/c/${CID}`, async (call) => {
      if (call.method === 'POST' && call.path.endsWith('/messages')) {
        server.attached = [doc(1)] // what the server will have stored
        return sseResponse([
          runStarted(),
          { type: 'document_created', document: doc(1, { status: 'pending' }) },
          token('Your PDF is on its way.'),
          usageEvent(),
          done(),
        ])
      }
      return handler(call)
    })
    await screen.findByRole('heading', { level: 1, name: 'Planning' })

    await user.type(screen.getByRole('textbox', { name: 'Message' }), '/pdf{Enter}')

    await screen.findByRole('region', { name: 'Document: Report 1' })
    await waitFor(() => expect(screen.queryByText('Stop')).not.toBeInTheDocument())
    await screen.findByText('Your PDF is on its way.')
    expect(screen.getAllByRole('region', { name: 'Document: Report 1' })).toHaveLength(1)
  })

  it('keeps the live card while the stream is open', async () => {
    const { server, handler } = fakeDocuments()
    server.rows = [doc(1, { status: 'pending' })]
    const stream = liveStream()
    const { user } = renderApp(`/c/${CID}`, (call) =>
      call.method === 'POST' && call.path.endsWith('/messages') ? stream.response : handler(call),
    )
    await screen.findByRole('heading', { level: 1, name: 'Planning' })
    await user.type(screen.getByRole('textbox', { name: 'Message' }), '/pdf{Enter}')

    stream.push(runStarted())
    stream.push({ type: 'document_created', document: doc(1, { status: 'pending' }) })

    const card = await screen.findByRole('region', { name: 'Document: Report 1' })
    expect(within(card).getByText('Preparing the PDF…')).toBeInTheDocument()
    stream.push(done())
    stream.end()
  })
})

describe('the documents page', () => {
  it('lists the documents with their status and a link back to the chat', async () => {
    const { server, handler } = fakeDocuments()
    server.rows = [
      doc(1, { title: 'Newest', kind: 'news_brief' }),
      doc(2, { title: 'Soon', status: 'pending' }),
      doc(3, { title: 'Broken', status: 'failed', error: 'Nothing usable.' }),
      doc(4, { title: 'Orphan', conversation_id: null }),
    ]
    renderApp('/documents', handler)

    expect(await screen.findByRole('heading', { level: 1, name: 'Documents' })).toBeVisible()
    const rows = within(await screen.findByRole('table'))
      .getAllByRole('row')
      .slice(1)
    expect(rows).toHaveLength(4)
    expect(within(rows[0]!).getByText('News brief')).toBeVisible()
    expect(within(rows[0]!).getByText('Ready')).toBeVisible()
    expect(within(rows[0]!).getByText('3 pages · 82 KB')).toBeVisible()
    expect(within(rows[0]!).getByRole('link', { name: 'Open the chat of Newest' })).toHaveAttribute(
      'href',
      `/c/${CID}`,
    )
    expect(within(rows[1]!).getByText('Preparing…')).toBeVisible()
    expect(within(rows[1]!).queryByRole('button', { name: /download/i })).not.toBeInTheDocument()
    expect(within(rows[2]!).getByText('Nothing usable.')).toBeVisible()
    expect(within(rows[3]!).queryByRole('link')).not.toBeInTheDocument()
  })

  it('has an empty state and a sidebar link', async () => {
    const { user } = renderApp('/', fakeDocuments().handler)

    await user.click(await screen.findByRole('link', { name: 'Documents' }))

    expect(await screen.findByText('No documents yet.')).toBeVisible()
  })

  it('refreshes a pending row until the PDF is ready', async () => {
    const { server, handler } = fakeDocuments()
    server.rows = [doc(1, { status: 'pending' })]
    renderApp('/documents', handler)
    expect(await screen.findByText('Preparing…')).toBeVisible()

    server.rows[0] = doc(1)

    expect(await screen.findByText('Ready')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Download Report 1' })).toBeVisible()
  })

  it('downloads from a row', async () => {
    const { server, handler } = fakeDocuments()
    server.rows = [doc(1)]
    const { user } = renderApp('/documents', handler)

    await user.click(await screen.findByRole('button', { name: 'Download Report 1' }))

    await waitFor(() => expect(saved.map((s) => s.filename)).toEqual(['Report 1.pdf']))
  })

  it('deletes after a confirmation, and Cancel keeps it', async () => {
    const { server, handler } = fakeDocuments()
    server.rows = [doc(1, { title: 'Keep me' }), doc(2, { title: 'Delete me' })]
    const { user } = renderApp('/documents', handler)
    await screen.findByText('Delete me')

    await user.click(screen.getByRole('button', { name: 'Delete Delete me' }))
    expect(screen.getByText(/The PDF will be removed/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByText('Delete me')).toBeVisible()
    expect(server.calls.some((c) => c.method === 'DELETE')).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Delete Delete me' }))
    await user.click(screen.getByRole('button', { name: 'Confirm delete Delete me' }))

    await waitFor(() => expect(screen.queryByText('Delete me')).not.toBeInTheDocument())
    expect(screen.getByText('Keep me')).toBeVisible()
    expect(server.calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual([
      `${location.origin}/api/v1/documents/${doc(2).id}`,
    ])
  })

  it('shows a failed delete next to the row and keeps the document', async () => {
    const { server, handler } = fakeDocuments({
      one: () => problem(500, 'INTERNAL_ERROR'),
    })
    server.rows = [doc(1)]
    const { user } = renderApp('/documents', handler)

    await user.click(await screen.findByRole('button', { name: 'Delete Report 1' }))
    await user.click(screen.getByRole('button', { name: 'Confirm delete Report 1' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByText('Report 1')).toBeVisible()
  })

  it('offers another try when the list cannot be read', async () => {
    let fail = true
    const { server, handler } = fakeDocuments()
    server.rows = [doc(1)]
    const { user } = renderApp('/documents', (call) =>
      fail && call.path.includes('/documents') ? problem(500, 'INTERNAL_ERROR') : handler(call),
    )

    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeVisible()
    fail = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Report 1')).toBeVisible()
  })

  it('needs a signed-in user', async () => {
    renderApp('/documents', (call) =>
      call.path.endsWith('/auth/refresh')
        ? problem(401, 'UNAUTHORIZED')
        : problem(401, 'UNAUTHORIZED'),
    )
    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeVisible()
  })
})
