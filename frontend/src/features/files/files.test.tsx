import { File as NodeFile } from 'node:buffer'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  authBody,
  emptyPage,
  json,
  problem,
  renderApp,
  type Call,
  type Handler,
} from '../../test-utils'
import type { FileObject } from './api'

const fileObj = (n: number, over: Partial<FileObject> = {}): FileObject => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  filename: `report-${n}.pdf`,
  mime_type: 'application/pdf',
  size_bytes: 2048,
  status: 'ready',
  error: null,
  page_count: 3,
  chunk_count: 7,
  created_at: '2026-11-20T10:00:00Z',
  ...over,
})

type Override = (call: Call) => Response | undefined

/** A small in-memory files API: newest first, uploads start `pending`. */
function fakeFiles(initial: FileObject[], override?: Override) {
  const rows = [...initial]
  let next = initial.length + 1
  const uploads: Call[] = []
  const handler: Handler = (call) => {
    if (call.path.endsWith('/auth/refresh')) return json(200, authBody())
    const early = override?.(call)
    if (early) return early
    const { pathname } = new URL(call.path, 'http://localhost')
    if (pathname.endsWith('/conversations')) return json(200, emptyPage)
    if (pathname.endsWith('/me/usage')) return problem(500, 'INTERNAL_ERROR')
    if (pathname.endsWith('/files') && call.method === 'GET') {
      return json(200, { items: rows, next_cursor: null })
    }
    if (pathname.endsWith('/files') && call.method === 'POST') {
      uploads.push(call)
      const created = fileObj(next++, {
        id: `00000000-0000-4000-8000-${String(900 + next).padStart(12, '0')}`,
        filename: 'uploaded.txt',
        status: 'pending',
        chunk_count: null,
        page_count: null,
      })
      rows.unshift(created)
      return json(202, created)
    }
    const id = pathname.split('/files/')[1]
    const row = rows.find((r) => r.id === id)
    if (call.method === 'DELETE') {
      if (!row) return problem(404, 'NOT_FOUND', 'File not found.')
      rows.splice(rows.indexOf(row), 1)
      return new Response(null, { status: 204 })
    }
    return problem(500, 'INTERNAL_ERROR')
  }
  return { rows, uploads, handler }
}

beforeEach(() => {
  document.documentElement.dataset.theme = 'light'
})

const chooser = () => screen.findByLabelText('Choose files to upload')
// Node's File, not jsdom's: the request is built by Node's fetch, which cannot read a jsdom File
// (it would wait forever). A browser has one File class, so this only matters in the tests.
const textFile = (name = 'notes.txt', body = 'Churn fell to 3.1 percent.') =>
  new NodeFile([body], name, { type: 'text/plain' }) as unknown as File

describe('files page', () => {
  it('lists files with size, status, chunks and the n/50 indicator', async () => {
    const api = fakeFiles([
      fileObj(1),
      fileObj(2, {
        filename: 'scan.pdf',
        status: 'failed',
        error: 'No extractable text.',
        chunk_count: null,
      }),
      fileObj(3, {
        filename: 'big.docx',
        status: 'processing',
        chunk_count: null,
        size_bytes: 3 * 1024 * 1024,
      }),
    ])
    renderApp('/files', api.handler)

    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('report-1.pdf')
    expect(rows[0]).toHaveTextContent('2 KB')
    expect(rows[0]).toHaveTextContent('Ready')
    expect(rows[0]).toHaveTextContent('7')
    expect(rows[1]).toHaveTextContent('Failed')
    expect(rows[1]).toHaveTextContent('No extractable text.')
    expect(rows[2]).toHaveTextContent('Processing…')
    expect(rows[2]).toHaveTextContent('3.0 MB')
    expect(screen.getByLabelText('Files used')).toHaveTextContent('3/50 files')
  })

  it('has an empty state', async () => {
    renderApp('/files', fakeFiles([]).handler)

    expect(await screen.findByText('No files yet.')).toBeInTheDocument()
    expect(screen.getByLabelText('Files used')).toHaveTextContent('0/50 files')
  })

  it('reports a list that cannot be loaded, with a retry', async () => {
    let fail = true
    const api = fakeFiles([fileObj(1)], (call) =>
      fail && call.method === 'GET' && call.path.includes('/files')
        ? problem(500, 'INTERNAL_ERROR')
        : undefined,
    )
    const { user } = renderApp('/files', api.handler)

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    fail = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('report-1.pdf')).toBeInTheDocument()
  })

  it('is reachable from the sidebar', async () => {
    const { user } = renderApp('/', fakeFiles([]).handler)

    await user.click(await screen.findByRole('link', { name: 'Files' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Files' })).toBeInTheDocument()
  })
})

describe('uploading', () => {
  it('sends the file as multipart, shows it Processing…, then Ready once the server says so', async () => {
    const api = fakeFiles([])
    const { user } = renderApp('/files', api.handler)
    await screen.findByText('No files yet.')

    await user.upload(await chooser(), textFile())

    expect(await screen.findByText('Processing…')).toBeInTheDocument()
    expect(api.uploads).toHaveLength(1)
    const sent = api.uploads[0]!
    expect(sent.headers.get('content-type')).toMatch(/^multipart\/form-data; boundary=/)
    expect(sent.rawBody).toBeInstanceOf(ArrayBuffer) // bytes, never text (see api/client.test.ts)

    api.rows[0]!.status = 'ready' // the worker finished
    api.rows[0]!.chunk_count = 1
    expect(await screen.findByText('Ready', {}, { timeout: 5000 })).toBeInTheDocument()
  })

  it('accepts files dropped on the page', async () => {
    const api = fakeFiles([])
    renderApp('/files', api.handler)
    await screen.findByText('No files yet.')

    fireEvent.drop(screen.getByText('Drop files here, or choose them'), {
      dataTransfer: { files: [textFile('dropped.txt')] },
    })

    await waitFor(() => expect(api.uploads).toHaveLength(1))
  })

  it('uploads at most 5 files at once and says the rest were skipped', async () => {
    const api = fakeFiles([])
    const { user } = renderApp('/files', api.handler)
    await screen.findByText('No files yet.')

    await user.upload(
      await chooser(),
      Array.from({ length: 7 }, (_, i) => textFile(`f${i}.txt`, `body ${i}`)),
    )

    await waitFor(() => expect(api.uploads).toHaveLength(5))
    expect(await screen.findByText(/up to 5 files at once/)).toBeInTheDocument()
  })

  it.each([
    ['FILE_TOO_LARGE', 413, 'That file is larger than 20 MB.'],
    ['FILE_TYPE_UNSUPPORTED', 415, 'Only PDF, DOCX, TXT and MD files are supported.'],
    ['FILE_DUPLICATE', 409, 'You have already uploaded this file.'],
    ['FILE_LIMIT_REACHED', 409, 'You can keep up to 50 files. Delete one to upload another.'],
    ['VALIDATION_FAILED', 422, 'That file is empty or could not be read.'],
  ])('shows the refusal %s on the file it concerns', async (code, status, text) => {
    const api = fakeFiles([], (call) =>
      call.method === 'POST' && call.path.endsWith('/files') ? problem(status, code) : undefined,
    )
    const { user } = renderApp('/files', api.handler)
    await screen.findByText('No files yet.')

    await user.upload(await chooser(), textFile('bad.txt'))

    const uploads = await screen.findByRole('list', { name: 'Uploads' })
    expect(within(uploads).getByText('bad.txt')).toBeInTheDocument()
    expect(within(uploads).getByText(text)).toBeInTheDocument()
  })

  it('shows a proxy refusal (413 without our error body) as too large', async () => {
    const api = fakeFiles([], (call) =>
      call.method === 'POST'
        ? new Response('Request Entity Too Large', { status: 413 })
        : undefined,
    )
    const { user } = renderApp('/files', api.handler)
    await screen.findByText('No files yet.')

    await user.upload(await chooser(), textFile())

    expect(await screen.findByText('That file is larger than 20 MB.')).toBeInTheDocument()
  })

  it('lets a failed upload be dismissed', async () => {
    const api = fakeFiles([], (call) =>
      call.method === 'POST' ? problem(409, 'FILE_DUPLICATE') : undefined,
    )
    const { user } = renderApp('/files', api.handler)
    await screen.findByText('No files yet.')
    await user.upload(await chooser(), textFile('dup.txt'))

    await user.click(await screen.findByRole('button', { name: 'Dismiss dup.txt' }))

    expect(screen.queryByRole('list', { name: 'Uploads' })).not.toBeInTheDocument()
  })
})

describe('deleting', () => {
  it('asks first, then deletes and drops the row', async () => {
    const api = fakeFiles([fileObj(1), fileObj(2)])
    const { user, calls } = renderApp('/files', api.handler)

    await user.click(await screen.findByRole('button', { name: 'Delete report-1.pdf' }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false) // nothing yet
    expect(screen.getByText(/Delete this file\?/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Confirm delete report-1.pdf' }))

    await waitFor(() => expect(screen.queryByText('report-1.pdf')).not.toBeInTheDocument())
    expect(screen.getByText('report-2.pdf')).toBeInTheDocument()
    expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual([
      expect.stringMatching(/\/files\/00000000-0000-4000-8000-000000000001$/),
    ])
    expect(screen.getByLabelText('Files used')).toHaveTextContent('1/50 files')
  })

  it('does nothing when the confirmation is cancelled', async () => {
    const api = fakeFiles([fileObj(1)])
    const { user, calls } = renderApp('/files', api.handler)

    await user.click(await screen.findByRole('button', { name: 'Delete report-1.pdf' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByRole('button', { name: 'Delete report-1.pdf' })).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
  })

  it('keeps the row and says why when the server fails', async () => {
    const api = fakeFiles([fileObj(1)], (call) =>
      call.method === 'DELETE' ? problem(500, 'INTERNAL_ERROR') : undefined,
    )
    const { user } = renderApp('/files', api.handler)

    await user.click(await screen.findByRole('button', { name: 'Delete report-1.pdf' }))
    await user.click(screen.getByRole('button', { name: 'Confirm delete report-1.pdf' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByText('report-1.pdf')).toBeInTheDocument()
  })
})

describe('the composer paperclip', () => {
  it('shows Uploading…, then Processing…, then Ready on the home page', async () => {
    const api = fakeFiles([])
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    const slow: Handler = async (call) => {
      if (call.method === 'POST' && call.path.endsWith('/files')) await gate
      return api.handler(call)
    }
    const { user } = renderApp('/', slow)

    await user.upload(await chooser(), textFile('q3.txt'))
    const chips = await screen.findByRole('list', { name: 'Uploads' })
    expect(chips).toHaveTextContent('q3.txt')
    expect(chips).toHaveTextContent('Uploading…')

    release()
    expect(await within(chips).findByText('Processing…')).toBeInTheDocument()
    api.rows[0]!.status = 'ready'
    expect(await within(chips).findByText('Ready', {}, { timeout: 5000 })).toBeInTheDocument()
  })

  it('shows a failed processing reason on the chip', async () => {
    const api = fakeFiles([])
    const { user } = renderApp('/', api.handler)

    await user.upload(await chooser(), textFile('scan.pdf'))
    await screen.findByText('Processing…')
    api.rows[0]!.status = 'failed'
    api.rows[0]!.error = 'File is password-protected.'

    expect(
      await screen.findByText('Failed: File is password-protected.', {}, { timeout: 5000 }),
    ).toBeInTheDocument()
  })

  it('does not call the files API until something was uploaded', async () => {
    const { calls } = renderApp('/', fakeFiles([]).handler)
    await screen.findByRole('heading', { level: 1, name: /Hello,/ })

    expect(calls.some((c) => c.path.includes('/files'))).toBe(false)
  })
})
