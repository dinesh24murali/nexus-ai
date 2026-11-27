import { File as NodeFile } from 'node:buffer'
import { describe, expect, it, vi } from 'vitest'
import { ApiProblem, type AuthManager } from '../lib/auth'
import { createApiClient, unwrap, unwrapEmpty } from './client'

type Sent = { url: string; init: RequestInit }

/** A manager whose `fetch` records what it was given and answers with `respond`. */
function fakeManager(respond: () => Response) {
  const sent: Sent[] = []
  const manager = {
    fetch: vi.fn(async (url: string, init: RequestInit = {}) => {
      sent.push({ url, init })
      return respond()
    }),
  } as unknown as AuthManager
  return { manager, sent }
}

const envelope = (status: number, code: string, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify({ error: { code, message: 'm', request_id: 'req_1' } }), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })

describe('api client adapter', () => {
  it('sends a JSON body as text, as before', async () => {
    const { manager, sent } = fakeManager(() => new Response('{}', { status: 201 }))

    await createApiClient(manager).POST('/conversations', { body: { title: 'Hi' } })

    expect(sent[0]!.init.body).toBe('{"title":"Hi"}')
  })

  it('sends a multipart upload as bytes with its boundary header, never as text', async () => {
    const { manager, sent } = fakeManager(() => new Response('{}', { status: 202 }))
    // Node's File: the request is built by Node's fetch, which cannot read a jsdom File. The
    // bytes on the wire are proven by the browser test (tests/e2e/rag.spec.ts); here the point is
    // the shape: an ArrayBuffer (text() would corrupt binary content) and the boundary header.
    const file = new NodeFile(['x'], 'b.pdf', { type: 'application/pdf' }) as unknown as File

    await createApiClient(manager).POST('/files', {
      body: { file: '' },
      bodySerializer: () => {
        const form = new FormData()
        form.append('file', file)
        return form
      },
    })

    const { init } = sent[0]!
    expect(init.body).toBeInstanceOf(ArrayBuffer)
    expect(new Headers(init.headers).get('content-type')).toMatch(
      /^multipart\/form-data; boundary=/,
    )
  })
})

describe('unwrap', () => {
  it('returns the data of a success', async () => {
    const { manager } = fakeManager(() => Response.json({ id: 'x' }))

    const data = await unwrap<unknown>(createApiClient(manager).GET('/me/usage'))

    expect(data).toEqual({ id: 'x' })
  })

  it('keeps the API error code, message details and Retry-After of a failure', async () => {
    const { manager } = fakeManager(() => envelope(409, 'FILE_DUPLICATE', { 'retry-after': '7' }))

    const failure = await unwrap(createApiClient(manager).GET('/me/usage')).catch((e: unknown) => e)

    expect(failure).toBeInstanceOf(ApiProblem)
    expect(failure).toMatchObject({ status: 409, code: 'FILE_DUPLICATE', retryAfterS: 7 })
  })

  it('reports a failure that is not our envelope as HTTP_ERROR', async () => {
    const { manager } = fakeManager(() => new Response('Bad Gateway', { status: 502 }))

    const failure = await unwrap(createApiClient(manager).GET('/me/usage')).catch((e: unknown) => e)

    expect(failure).toMatchObject({ status: 502, code: 'HTTP_ERROR' })
  })
})

describe('unwrapEmpty', () => {
  it('resolves on 204', async () => {
    const { manager } = fakeManager(() => new Response(null, { status: 204 }))

    await expect(
      unwrapEmpty(
        createApiClient(manager).DELETE('/files/{fileId}', { params: { path: { fileId: 'a' } } }),
      ),
    ).resolves.toBeUndefined()
  })

  it('throws the API error code on failure', async () => {
    const { manager } = fakeManager(() => envelope(404, 'NOT_FOUND'))

    const failure = await unwrapEmpty(
      createApiClient(manager).DELETE('/files/{fileId}', { params: { path: { fileId: 'a' } } }),
    ).catch((e: unknown) => e)

    expect(failure).toMatchObject({ status: 404, code: 'NOT_FOUND' })
  })
})
